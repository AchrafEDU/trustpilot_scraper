# Multi-Processed Async Trustpilot Scraper

This repository contains a high-performance, resilient, and scaled scraper built to extract business information from Trustpilot. It utilizes Python's multiprocessing to parallelize operations across input parts, cooperative asyncio to load pages concurrently within each process, and Hugging Face API to upload outputs securely using your Hugging Face access token.

## System Architecture

- **Multiprocessing**: The orchestrator splits the execution of split CSV parts across multiple processes (`--num-processes`). Each process runs its own chromium instance via Playwright to ensure optimal CPU usage and isolation.
- **Asynchronous Concurrency**: Within each worker process, pages are fetched concurrently using `asyncio.gather` up to the configured limit (`--concurrency`).
- **Rate Limiting**: To prevent rate limiting and blocks, each process maintains an independent async rate limiter enforcing a configurable delay between requests.
- **Hugging Face Publishing**: Authenticates securely using your Hugging Face Access Token configured as a repository secret on GitHub.
- **Storage Management**: To keep the Git repository storage footprint small and stay well below the 100MB repository limit, output CSV part files are immediately deleted from the workspace as soon as they are successfully uploaded to Hugging Face. Checkpoints (tiny text JSON files) are committed to Git to track execution state.

---

## Setup and Installation

### Prerequisites
- Python 3.10 or higher
- Playwright browser dependencies

### Local Installation
1. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```
2. Install the Chromium browser driver:
   ```bash
   python -m playwright install chromium
   ```

---

## Usage

Run the scraper using the python interpreter with the following command-line flags:

```bash
python trustpilot_scraper.py [options]
```

### Options

- `-p`, `--num-processes`: Number of parallel worker processes (default: 2).
- `-c`, `--concurrency`: Number of concurrent pages handled in each process (default: 5).
- `--input-dir`: Path to the directory containing split input CSVs (default: `data_locale/split_cleaned_company_names`).
- `--output-dir`: Path to the directory for saving results and checkpoints (default: `./results`).
- `--rate-limit`: Waiting delay in seconds between page requests within each process (default: 2.5).
- `--max-parts`: Limit the number of CSV parts to process in this run.
- `--headless`: Run browser headlessly (`true` or `false`, default: `true`).
- `--hf-repo-id`: Hugging Face Dataset repository identifier (e.g. `username/dataset-name`).
- `--rerun-failed`: Rerun logic that retries companies that failed in previous runs due to timeout or challenge pages.

### Example Commands

- Run scraping locally with 4 parallel processes:
  ```bash
  python trustpilot_scraper.py --num-processes 4 --concurrency 5
  ```
- Rerun failed records and push outputs to Hugging Face:
  ```bash
  python trustpilot_scraper.py --rerun-failed --hf-repo-id your-username/dataset-name
  ```

---

## CI/CD Workflows

The repository includes two GitHub Actions workflows configured in the `.github/workflows/` directory:

### 1. Short-term Scraper Test (`test.yml`)
- **Triggers**: Executed automatically on push and pull requests targeting master/main branches.
- **Purpose**: Runs a quick test cycle against the mock CSV file `test_input_dir/cleaned_company_names_part_999.csv` to ensure all scraping, parsing, and execution paths work correctly without uploading results.

### 2. Production Scraper Execution (`scrape.yml`)
- **Triggers**: Runs automatically every 5 hours and can be manually dispatched via the GitHub UI.
- **Purpose**: Runs full multiprocessing execution. It interacts with Hugging Face via your repository token, downloads previously completed parts, runs the scraper, pushes the updated results, cleans up local CSV files, and commits checkpoints back to the repository using short-lived tokens.

---

## Configuring Hugging Face Authentication

To enable uploads to Hugging Face, configure the repository secret:
1. Go to your GitHub repository, click on **Settings** -> **Secrets and variables** -> **Actions**.
2. Create a new repository secret named `HG_ACCESS_TOKEN` and set its value to your Hugging Face Access Token.
3. Update the `HF_REPO_ID` environment variable in `.github/workflows/scrape.yml` or supply it when manually triggering the workflow.
