import json

from pathlib import Path
from typing import Any

from loguru import logger


class CheckpointManager:
    """Manages processing checkpoints to resume safely after interruptions."""

    def __init__(self, filepath: str = "checkpoint.json") -> None:
        """Initialize the checkpoint manager.

        Args:
            filepath: Path to the JSON file storing checkpoint state.
        """
        self.filepath = Path(filepath)
        self.state: dict[str, Any] = {"current_file": None, "processed_rows": 0}
        self.load()

    def load(self) -> None:
        """Load state from checkpoint file if it exists."""
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
                logger.info(f"Resuming from checkpoint: {self.state}")
            except Exception as e:
                logger.error(f"Failed to load checkpoint, starting fresh: {e}")
        else:
            logger.info("No checkpoint found, starting fresh.")

    def save(self) -> None:
        """Save current state to checkpoint file."""
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def update(self, current_file: str, processed_rows: int) -> None:
        """Update the checkpoint state and save.

        Args:
            current_file: The name or path of the current CSV being processed.
            processed_rows: The number of rows processed in the current CSV.
        """
        self.state["current_file"] = current_file
        self.state["processed_rows"] = processed_rows
        self.save()

    def get_last_file(self) -> str | None:
        """Get the last processed file from the checkpoint."""
        return self.state.get("current_file")

    def get_processed_rows(self) -> int:
        """Get the number of processed rows for the current file."""
        return self.state.get("processed_rows", 0)
