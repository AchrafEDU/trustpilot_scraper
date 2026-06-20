# Scraper Persistent Memory

This file serves as a reference for future AI agents or developers continuing work on this codebase.

## Repository State
- **Main Scraper Script**: [trustpilot_scraper.py](file:///d:/Trustpilot/trustpilot_scraper.py) is the master coordinator.
- **Scraper Helpers**:
  - [scraper.py](file:///d:/Trustpilot/src/scraper.py) manages raw fetches, BeautifulSoup parsing, and rate limiting.
  - [parser.py](file:///d:/Trustpilot/src/parser.py) handles extracting structured business metrics from Trustpilot next-data blocks.
  - [checkpoint.py](file:///d:/Trustpilot/src/checkpoint.py) handles saving execution milestones.
  - [rate_limiter.py](file:///d:/Trustpilot/src/rate_limiter.py) enforces delay between requests.
- **Workflow Files**:
  - [.github/workflows/test.yml](file:///d:/Trustpilot/.github/workflows/test.yml) runs code sanity testing.
  - [.github/workflows/scrape.yml](file:///d:/Trustpilot/.github/workflows/scrape.yml) runs production cron jobs every 5 hours.

## Key Technical Architectures
- **Multiprocessing**: Parallelization happens on the level of input CSV parts. Each process parses one file, running its own asyncio loop and Chromium browser page manager.
- **Resume and Append**: If a part was previously processed, it uses a checkpoint file `checkpoint_part_X.json` to resume execution.
- **Hugging Face Hub Integration**:
  - The script uses the `huggingface_hub` package.
  - Authenticates securely via the `HF_TOKEN` environment variable, which is populated from the GitHub repository secret `HG_ACCESS_TOKEN` in workflows.
  - If a part is processed, the script downloads the existing file from Hugging Face first (if missing locally), appends newly scraped items, uploads it, and then deletes the local copy. This prevents Git repositories from exceeding 100MB while keeping full historical data on Hugging Face. Additionally, `results/*.csv` is explicitly ignored in `.gitignore` to prevent any accidental commits of output data to git history.

## Guidelines for Future Work
- Do not add emojis to any documentation, logs, or commit messages.
- Maintain strict separation of concerns: scraper handles connection/CF-block logic, parser parses HTML, checkpoint tracks progress, and orchestrator runs multiprocessing.
- Always use relative packages imports inside the `src/` folder.
- Ensure that the GITHUB_TOKEN has write contents permission in workflow setups.
