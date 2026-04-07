"""
File management — export tasks and summaries as PDF or plain-text files.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fpdf import FPDF

import config
from assistant import storage, tasks as task_module

logger = logging.getLogger(__name__)


# ── PDF helper ────────────────────────────────────────────────────────────────

class _PDF(FPDF):
    """Thin FPDF subclass with a consistent header and footer."""

    def __init__(self, title: str = "AI Personal Assistant Report"):
        super().__init__()
        self._doc_title = title

    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, self._doc_title, ln=True, align="C")
        self.set_font("Helvetica", "", 9)
        self.cell(0, 6, f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", ln=True, align="C")
        self.ln(4)
        self.line(self.get_x(), self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


# ── Exports ───────────────────────────────────────────────────────────────────

def export_tasks_pdf(
    user_tasks: List[Dict[str, Any]],
    filename: Optional[str] = None,
) -> Path:
    """
    Export *user_tasks* as a PDF and return the file path.
    """
    pdf = _PDF(title="Task Report")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 11)

    if not user_tasks:
        pdf.cell(0, 8, "No tasks found.", ln=True)
    else:
        priority_map = {1: "High", 2: "Medium", 3: "Low"}
        for task in user_tasks:
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(
                0,
                7,
                f"[#{task['id']}] {task['title']}",
            )
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(0, 6, f"  Status   : {task.get('status', 'pending')}", ln=True)
            pdf.cell(0, 6, f"  Category : {task.get('category', 'general')}", ln=True)
            pdf.cell(
                0, 6,
                f"  Priority : {priority_map.get(task.get('priority', 2), 'Medium')}",
                ln=True,
            )
            if task.get("deadline"):
                pdf.cell(0, 6, f"  Deadline : {task['deadline']}", ln=True)
            if task.get("description"):
                pdf.multi_cell(0, 6, f"  Notes    : {task['description']}")
            pdf.ln(3)

    exports_dir = Path(config.EXPORTS_DIR)
    exports_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        filename = f"tasks_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
    filepath = exports_dir / filename
    pdf.output(str(filepath))
    logger.info("Exported %d tasks to %s", len(user_tasks), filepath)
    return filepath


def export_summary_pdf(
    summary_text: str,
    filename: Optional[str] = None,
) -> Path:
    """
    Export a summary string as a PDF and return the file path.
    """
    pdf = _PDF(title="Conversation Summary")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 8, summary_text)

    exports_dir = Path(config.EXPORTS_DIR)
    exports_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        filename = f"summary_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pdf"
    filepath = exports_dir / filename
    pdf.output(str(filepath))
    logger.info("Exported summary to %s", filepath)
    return filepath


def export_tasks_text(
    user_tasks: List[Dict[str, Any]],
    filename: Optional[str] = None,
) -> Path:
    """
    Export *user_tasks* as a plain-text file and return the file path.
    """
    exports_dir = Path(config.EXPORTS_DIR)
    exports_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        filename = f"tasks_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt"
    filepath = exports_dir / filename

    content = task_module.format_task_list(user_tasks)
    filepath.write_text(content, encoding="utf-8")
    logger.info("Exported %d tasks (text) to %s", len(user_tasks), filepath)
    return filepath
