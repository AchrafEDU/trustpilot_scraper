import argparse
import asyncio
import csv
import os
import re
import sys
import time
from dataclasses import asdict, fields
from concurrent.futures import ProcessPoolExecutor, as_completed

from loguru import logger
from playwright.async_api import BrowserContext, async_playwright
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.utils import EntryNotFoundError, RepositoryNotFoundError

from src.checkpoint import CheckpointManager
from src.models import Business
from src.parser import parse_business_page
from src.scraper import BotBlockException, fetch_page, search_company
from src.rate_limiter import global_rate_limiter


def load_env() -> None:
    """Loads environment variables from .env if present and maps HG_ACCESS_TOKEN to HF_TOKEN."""
    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and v and k not in os.environ:
                            os.environ[k] = v
        except Exception as e:
            logger.warning(f"Failed to load .env file: {e}")

    # Fallback/mapping for HG_ACCESS_TOKEN
    if "HG_ACCESS_TOKEN" in os.environ and "HF_TOKEN" not in os.environ:
        os.environ["HF_TOKEN"] = os.environ["HG_ACCESS_TOKEN"]


load_env()


def parse_args():
    """Parses command line arguments."""
    parser = argparse.ArgumentParser(description="Multi-processed Async Trustpilot Scraper")
    parser.add_argument(
        "--num-processes", "-p", type=int, default=2, help="Number of parallel processes to run (default: 2)"
    )
    parser.add_argument(
        "--concurrency", "-c", type=int, default=5, help="Number of concurrent pages per process (default: 5)"
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default="data_locale/split_cleaned_company_names",
        help="Directory containing split company name CSV files",
    )
    parser.add_argument(
        "--output-dir", type=str, default="./results", help="Directory where output CSVs and checkpoints will be saved"
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=2.5,
        help="Rate limit delay in seconds between requests per process (default: 2.5)",
    )
    parser.add_argument("--max-parts", type=int, default=None, help="Maximum number of parts to process in this run")
    parser.add_argument(
        "--rerun-failed", action="store_true", help="Rerun failed companies from failed_companies_part_X.csv files"
    )
    parser.add_argument(
        "--headless",
        type=str,
        choices=["true", "false"],
        default="true",
        help="Run browser in headless mode (default: true)",
    )
    parser.add_argument(
        "--hf-repo-id",
        type=str,
        default=os.environ.get("HF_REPO_ID"),
        help="Hugging Face Dataset repository ID (e.g. 'org/dataset-name')",
    )
    parser.add_argument("--max-duration", type=int, default=None, help="Maximum execution duration in seconds")
    return parser.parse_args()


def save_csv_append(records: list[dict], path: str) -> None:
    """Appends records to the CSV, creating the file and header if it doesn't exist."""
    if not records:
        return
    keys = [f.name for f in fields(Business)]
    file_exists = os.path.exists(path)

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        if not file_exists:
            writer.writeheader()
        writer.writerows(records)


def save_failed_csv_append(records: list[dict], path: str) -> None:
    """Appends failed company logs to the CSV, creating the file and header if it doesn't exist."""
    if not records:
        return
    keys = ["company_name", "error_type", "error_message", "timestamp"]
    file_exists = os.path.exists(path)

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        if not file_exists:
            writer.writeheader()
        writer.writerows(records)


def download_results_from_hf(hf_repo_id: str, output_csv: str, part_num: int) -> None:
    """Downloads existing results for this part from Hugging Face if not present locally."""
    if not os.path.exists(output_csv):
        filename = f"trustpilot_results_part_{part_num}.csv"
        try:
            logger.info(f"[Part {part_num}] Local results CSV not found. Checking Hugging Face dataset: {hf_repo_id}")
            token = os.environ.get("HF_TOKEN")
            downloaded_path = hf_hub_download(
                repo_id=hf_repo_id,
                filename=filename,
                repo_type="dataset",
                local_dir=os.path.dirname(output_csv),
                token=token,
            )
            norm_downloaded = os.path.abspath(os.path.normpath(downloaded_path))
            norm_output = os.path.abspath(os.path.normpath(output_csv))
            if norm_downloaded != norm_output:
                if os.path.exists(norm_output):
                    os.remove(norm_output)
                os.rename(norm_downloaded, norm_output)
            logger.info(f"[Part {part_num}] Successfully downloaded {filename} from Hugging Face.")
        except (EntryNotFoundError, RepositoryNotFoundError) as e:
            logger.info(f"[Part {part_num}] No pre-existing results file found on Hugging Face: {e}")
        except Exception as e:
            logger.error(f"[Part {part_num}] Failed to check/download pre-existing results from Hugging Face: {e}")
            raise


def upload_results_to_hf(hf_repo_id: str, output_csv: str, part_num: int) -> None:
    """Uploads results for this part to Hugging Face dataset and deletes local file if successful."""
    if os.path.exists(output_csv):
        filename = f"trustpilot_results_part_{part_num}.csv"
        try:
            logger.info(f"[Part {part_num}] Uploading results to Hugging Face dataset: {hf_repo_id}")
            token = os.environ.get("HF_TOKEN")
            api = HfApi(token=token)
            api.create_repo(repo_id=hf_repo_id, repo_type="dataset", exist_ok=True)
            api.upload_file(path_or_fileobj=output_csv, path_in_repo=filename, repo_id=hf_repo_id, repo_type="dataset")
            logger.info(f"[Part {part_num}] Successfully uploaded {filename} to Hugging Face. Removing local copy.")
            os.remove(output_csv)
        except Exception as e:
            logger.error(f"[Part {part_num}] Failed to upload results to Hugging Face: {e}")


async def process_company(context: BrowserContext, company_name: str, filename: str, row_index: int) -> dict:
    """Handles the end-to-end processing of a single company, returning success, no_results, or failed status."""
    logger.info(f"[{filename}:{row_index}] Searching for: {company_name}")

    try:
        biz_url = await search_company(context, company_name)
        if not biz_url:
            logger.info(f"[{filename}:{row_index}] No Trustpilot page found for: {company_name}")
            return {"status": "no_results", "company_name": company_name}

        logger.info(f"[{filename}:{row_index}] Fetching page: {biz_url}")
        html = await fetch_page(context, biz_url, wait_selector="h1")

        if html:
            if "Verifying your connection" in html or "Just a moment" in html:
                raise BotBlockException("Bot block detected in parsed HTML content")

            biz = parse_business_page(html, biz_url=biz_url, source_url=company_name)
            if biz:
                logger.info(f"[{filename}:{row_index}] Scraped details for: {biz.business_name}")
                return {"status": "success", "data": asdict(biz)}

        return {
            "status": "failed",
            "error_type": "PARSE_FAILED",
            "error_message": "Failed to parse business page HTML",
            "company_name": company_name,
        }

    except BotBlockException as e:
        logger.warning(f"[{filename}:{row_index}] Bot block for {company_name}: {e}")
        return {"status": "failed", "error_type": "BOT_BLOCK", "error_message": str(e), "company_name": company_name}
    except Exception as e:
        logger.error(f"[{filename}:{row_index}] Exception processing {company_name}: {e}")
        return {
            "status": "failed",
            "error_type": "EXCEPTION",
            "error_message": f"{type(e).__name__}: {str(e)}",
            "company_name": company_name,
        }


async def process_batch(
    context: BrowserContext,
    batch: list[tuple[int, str]],
    filename: str,
    checkpoint: CheckpointManager,
    output_csv: str,
    failed_csv: str,
) -> None:
    """Processes a batch of companies concurrently, writes output and updates checkpoint."""
    tasks = [process_company(context, company_name, filename, row_idx) for row_idx, company_name in batch]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid_records = []
    failed_records = []

    for (row_idx, company_name), r in zip(batch, results):
        if isinstance(r, Exception):
            logger.error(f"[{filename}:{row_idx}] Batch task failed with exception: {r}")
            failed_records.append(
                {
                    "company_name": company_name,
                    "error_type": "UNHANDLED_EXCEPTION",
                    "error_message": str(r),
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        elif isinstance(r, dict):
            if r["status"] == "success":
                valid_records.append(r["data"])
            elif r["status"] == "failed":
                failed_records.append(
                    {
                        "company_name": r["company_name"],
                        "error_type": r["error_type"],
                        "error_message": r["error_message"],
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )

    if valid_records:
        await asyncio.to_thread(save_csv_append, valid_records, output_csv)

    if failed_records:
        await asyncio.to_thread(save_failed_csv_append, failed_records, failed_csv)

    max_row_idx = max(row_idx for row_idx, _ in batch)
    checkpoint.update(filename, max_row_idx + 1)


async def async_run_part(
    part_num: int, input_filepath: str, output_csv: str, failed_csv: str, checkpoint_filepath: str, options: dict
) -> None:
    """Core asynchronous function for running a single part."""
    filename = os.path.basename(input_filepath)
    logger.info(f"[Part {part_num}] Starting processing for {filename}")

    global_rate_limiter.delay_seconds = options.get("rate_limit", 2.5)
    hf_repo_id = options.get("hf_repo_id")

    if hf_repo_id:
        download_results_from_hf(hf_repo_id, output_csv, part_num)

    checkpoint = CheckpointManager(checkpoint_filepath)
    start_row = checkpoint.get_processed_rows()

    if checkpoint.state.get("completed", False):
        logger.info(f"[Part {part_num}] File {filename} already completed. Skipping.")
        if hf_repo_id:
            upload_results_to_hf(hf_repo_id, output_csv, part_num)
        return

    if not os.path.exists(input_filepath):
        logger.error(f"[Part {part_num}] Input file not found: {input_filepath}")
        return

    companies = []
    with open(input_filepath, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        try:
            next(reader)
        except StopIteration:
            pass
        else:
            for idx, row in enumerate(reader):
                if row:
                    companies.append((idx, row[0]))

    total_companies = len(companies)
    logger.info(
        f"[Part {part_num}] {filename} has {total_companies} companies to process (resuming at row {start_row})"
    )

    if start_row >= total_companies:
        logger.info(f"[Part {part_num}] Already processed all rows. Marking completed.")
        checkpoint.state["completed"] = True
        checkpoint.save()
        if hf_repo_id:
            upload_results_to_hf(hf_repo_id, output_csv, part_num)
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=options.get("headless", True), args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        batch = []
        concurrency_limit = options.get("concurrency_limit", 5)
        max_end_time = options.get("max_end_time")
        time_limit_exceeded = False

        for current_row_index, company_name in companies:
            if current_row_index < start_row:
                continue

            if max_end_time and time.time() > max_end_time:
                logger.warning(f"[Part {part_num}] Time limit reached. Stopping gracefully.")
                time_limit_exceeded = True
                break

            batch.append((current_row_index, company_name))

            if len(batch) >= concurrency_limit:
                await process_batch(context, batch, filename, checkpoint, output_csv, failed_csv)
                batch = []

        if batch:
            await process_batch(context, batch, filename, checkpoint, output_csv, failed_csv)

        if not time_limit_exceeded:
            checkpoint.state["completed"] = True
            checkpoint.save()
            logger.info(f"[Part {part_num}] Completed file {filename}")
        else:
            logger.info(
                f"[Part {part_num}] Stopped early due to time limit. Checkpoint saved at row {checkpoint.get_processed_rows()}"
            )

        await browser.close()

    if hf_repo_id:
        upload_results_to_hf(hf_repo_id, output_csv, part_num)


def run_part_worker(
    part_num: int, input_filepath: str, output_csv: str, failed_csv: str, checkpoint_filepath: str, options: dict
) -> None:
    """Sync wrapper to run async_run_part in a separate process."""
    try:
        asyncio.run(async_run_part(part_num, input_filepath, output_csv, failed_csv, checkpoint_filepath, options))
    except Exception as e:
        logger.exception(f"Worker process for part {part_num} failed: {e}")


def finalize_rerun_files(failed_csv: str, temp_failed_csv: str, checkpoint_filepath: str) -> None:
    """Replaces the original failed CSV with the new failures, deletes temp files and checkpoint."""
    if os.path.exists(temp_failed_csv):
        if os.path.exists(failed_csv):
            try:
                os.remove(failed_csv)
            except Exception as e:
                logger.error(f"Failed to remove {failed_csv}: {e}")
        try:
            os.rename(temp_failed_csv, failed_csv)
            logger.info(f"Updated {failed_csv} with remaining failures.")
        except Exception as e:
            logger.error(f"Failed to rename {temp_failed_csv} to {failed_csv}: {e}")
    else:
        if os.path.exists(failed_csv):
            try:
                os.remove(failed_csv)
                logger.info(f"All retried companies succeeded. Removed {failed_csv}.")
            except Exception as e:
                logger.error(f"Failed to remove {failed_csv}: {e}")

    if os.path.exists(checkpoint_filepath):
        try:
            os.remove(checkpoint_filepath)
        except Exception as e:
            logger.error(f"Failed to remove checkpoint {checkpoint_filepath}: {e}")


async def async_run_failed_rerun(
    part_num: int, output_csv: str, failed_csv: str, checkpoint_filepath: str, temp_failed_csv: str, options: dict
) -> None:
    """Core asynchronous function for running a failed companies rerun."""
    filename = os.path.basename(failed_csv)
    logger.info(f"[Part {part_num} Rerun] Starting rerun for {filename}")

    if not os.path.exists(failed_csv):
        logger.info(f"[Part {part_num} Rerun] No failed companies file found for this part. Skipping.")
        return

    global_rate_limiter.delay_seconds = options.get("rate_limit", 2.5)
    hf_repo_id = options.get("hf_repo_id")

    if hf_repo_id:
        download_results_from_hf(hf_repo_id, output_csv, part_num)

    companies = []
    with open(failed_csv, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        try:
            next(reader)
        except StopIteration:
            pass
        else:
            for idx, row in enumerate(reader):
                if row:
                    companies.append((idx, row[0]))

    total_companies = len(companies)
    if total_companies == 0:
        logger.info(f"[Part {part_num} Rerun] No companies in {filename} to retry. Deleting empty file.")
        try:
            os.remove(failed_csv)
        except Exception:
            pass
        return

    checkpoint = CheckpointManager(checkpoint_filepath)
    start_row = checkpoint.get_processed_rows()

    if checkpoint.state.get("completed", False):
        logger.info(f"[Part {part_num} Rerun] Already marked as completed. Finalizing files.")
        finalize_rerun_files(failed_csv, temp_failed_csv, checkpoint_filepath)
        if hf_repo_id:
            upload_results_to_hf(hf_repo_id, output_csv, part_num)
        return

    logger.info(
        f"[Part {part_num} Rerun] {filename} has {total_companies} failed companies to retry (resuming at row {start_row})"
    )

    if start_row >= total_companies:
        logger.info(f"[Part {part_num} Rerun] Already processed all rows. Finalizing.")
        checkpoint.state["completed"] = True
        checkpoint.save()
        finalize_rerun_files(failed_csv, temp_failed_csv, checkpoint_filepath)
        if hf_repo_id:
            upload_results_to_hf(hf_repo_id, output_csv, part_num)
        return

    time_limit_exceeded = False
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=options.get("headless", True), args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        batch = []
        concurrency_limit = options.get("concurrency_limit", 5)
        max_end_time = options.get("max_end_time")

        for current_row_index, company_name in companies:
            if current_row_index < start_row:
                continue

            if max_end_time and time.time() > max_end_time:
                logger.warning(f"[Part {part_num} Rerun] Time limit reached. Stopping gracefully.")
                time_limit_exceeded = True
                break

            batch.append((current_row_index, company_name))

            if len(batch) >= concurrency_limit:
                await process_batch(context, batch, filename, checkpoint, output_csv, temp_failed_csv)
                batch = []

        if batch:
            await process_batch(context, batch, filename, checkpoint, output_csv, temp_failed_csv)

        if not time_limit_exceeded:
            checkpoint.state["completed"] = True
            checkpoint.save()
        await browser.close()

    if not time_limit_exceeded:
        finalize_rerun_files(failed_csv, temp_failed_csv, checkpoint_filepath)
        logger.info(f"[Part {part_num} Rerun] Completed retry for {filename}")
    else:
        logger.info(f"[Part {part_num} Rerun] Stopped early due to time limit. Checkpoint saved.")

    if hf_repo_id:
        upload_results_to_hf(hf_repo_id, output_csv, part_num)


def run_failed_rerun_worker(
    part_num: int, output_csv: str, failed_csv: str, checkpoint_filepath: str, temp_failed_csv: str, options: dict
) -> None:
    """Sync wrapper to run async_run_failed_rerun in a separate process."""
    try:
        asyncio.run(
            async_run_failed_rerun(part_num, output_csv, failed_csv, checkpoint_filepath, temp_failed_csv, options)
        )
    except Exception as e:
        logger.exception(f"Rerun worker process for part {part_num} failed: {e}")


def run_normal_orchestrator(args, options: dict) -> None:
    """Orchestrates normal scraping across input CSV parts using multiprocessing."""
    if not os.path.exists(args.input_dir):
        logger.error(f"Input directory not found: {args.input_dir}")
        return

    all_files = [f for f in os.listdir(args.input_dir) if f.endswith(".csv")]

    def extract_part(f_name: str) -> int:
        match = re.search(r"_part_(\d+)\.csv$", f_name)
        if match:
            return int(match.group(1))
        return 0

    all_files.sort(key=extract_part)

    files_to_process = []
    for f in all_files:
        part_num = extract_part(f)
        checkpoint_filepath = os.path.join(args.output_dir, f"checkpoint_part_{part_num}.json")
        if os.path.exists(checkpoint_filepath):
            chk = CheckpointManager(checkpoint_filepath)
            if chk.state.get("completed", False):
                logger.debug(f"Part {part_num} already completed. Skipping.")
                continue

        files_to_process.append((part_num, f))

    if args.max_parts is not None:
        files_to_process = files_to_process[: args.max_parts]

    if not files_to_process:
        logger.info("No parts left to process.")
        return

    logger.info(
        f"Found {len(files_to_process)} parts to process. Launching up to {args.num_processes} parallel processes."
    )

    with ProcessPoolExecutor(max_workers=args.num_processes) as executor:
        futures = {}
        for part_num, filename in files_to_process:
            input_filepath = os.path.join(args.input_dir, filename)
            output_csv = os.path.join(args.output_dir, f"trustpilot_results_part_{part_num}.csv")
            failed_csv = os.path.join(args.output_dir, f"failed_companies_part_{part_num}.csv")
            checkpoint_filepath = os.path.join(args.output_dir, f"checkpoint_part_{part_num}.json")

            fut = executor.submit(
                run_part_worker, part_num, input_filepath, output_csv, failed_csv, checkpoint_filepath, options
            )
            futures[fut] = part_num

        for fut in as_completed(futures):
            part_num = futures[fut]
            try:
                fut.result()
                logger.info(f"Process for part {part_num} finished successfully.")
            except Exception as e:
                logger.error(f"Process for part {part_num} failed with error: {e}")


def run_rerun_orchestrator(args, options: dict) -> None:
    """Orchestrates rerunning failed companies across output CSV parts using multiprocessing."""
    all_files = [
        f for f in os.listdir(args.output_dir) if f.startswith("failed_companies_part_") and f.endswith(".csv")
    ]

    def extract_part(f_name: str) -> int:
        match = re.search(r"failed_companies_part_(\d+)\.csv$", f_name)
        if match:
            return int(match.group(1))
        return 0

    all_files.sort(key=extract_part)

    files_to_process = []
    for f in all_files:
        part_num = extract_part(f)
        checkpoint_filepath = os.path.join(args.output_dir, f"checkpoint_failed_part_{part_num}.json")
        if os.path.exists(checkpoint_filepath):
            chk = CheckpointManager(checkpoint_filepath)
            if chk.state.get("completed", False):
                temp_failed_csv = os.path.join(args.output_dir, f"failed_companies_part_{part_num}_temp.csv")
                failed_csv = os.path.join(args.output_dir, f)
                finalize_rerun_files(failed_csv, temp_failed_csv, checkpoint_filepath)
                continue

        files_to_process.append((part_num, f))

    if args.max_parts is not None:
        files_to_process = files_to_process[: args.max_parts]

    if not files_to_process:
        logger.info("No failed files found or left to process.")
        return

    logger.info(
        f"Found {len(files_to_process)} failed files to process. Launching up to {args.num_processes} parallel processes."
    )

    with ProcessPoolExecutor(max_workers=args.num_processes) as executor:
        futures = {}
        for part_num, filename in files_to_process:
            output_csv = os.path.join(args.output_dir, f"trustpilot_results_part_{part_num}.csv")
            failed_csv = os.path.join(args.output_dir, filename)
            checkpoint_filepath = os.path.join(args.output_dir, f"checkpoint_failed_part_{part_num}.json")
            temp_failed_csv = os.path.join(args.output_dir, f"failed_companies_part_{part_num}_temp.csv")

            fut = executor.submit(
                run_failed_rerun_worker, part_num, output_csv, failed_csv, checkpoint_filepath, temp_failed_csv, options
            )
            futures[fut] = part_num

        for fut in as_completed(futures):
            part_num = futures[fut]
            try:
                fut.result()
                logger.info(f"Rerun process for part {part_num} finished successfully.")
            except Exception as e:
                logger.error(f"Rerun process for part {part_num} failed with error: {e}")


def main() -> None:
    start_time = time.time()
    try:
        import multiprocessing

        multiprocessing.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    args = parse_args()

    logger.remove()
    logger.add(sys.stderr, level="INFO")

    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir, exist_ok=True)

    logger.add(os.path.join(args.output_dir, "scraper.log"), rotation="10 MB", level="DEBUG")

    logger.info("Starting Scaled Trustpilot Scraper Orchestrator")

    max_end_time = start_time + args.max_duration if args.max_duration else None

    options = {
        "concurrency_limit": args.concurrency,
        "rate_limit": args.rate_limit,
        "headless": args.headless == "true",
        "hf_repo_id": args.hf_repo_id,
        "max_end_time": max_end_time,
    }

    if args.rerun_failed:
        logger.info("Rerun failed mode activated. Scanning for failed CSV files...")
        run_rerun_orchestrator(args, options)
    else:
        logger.info("Normal scraping mode activated. Scanning for input CSV files...")
        run_normal_orchestrator(args, options)


if __name__ == "__main__":
    main()
