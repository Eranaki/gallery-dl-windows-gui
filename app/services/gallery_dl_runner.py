from __future__ import annotations

import importlib.util
import shlex
import shutil
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from app.models.task import DownloadTask, MediaScope, TaskMode, TaskStatus


IMAGE_EXTENSIONS = ("jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "avif", "jxl")
VIDEO_EXTENSIONS = ("mp4", "mkv", "webm", "avi", "mov", "wmv", "m4v", "flv")


@dataclass(slots=True)
class ResolvedCommand:
    program: str
    prefix_args: list[str]
    display: str


class GalleryDlRunner(QObject):
    task_changed = Signal(object)
    task_output = Signal(str, str, str)
    queue_state_changed = Signal(bool)
    current_task_changed = Signal(object)

    def __init__(self, gallery_dl_path: str) -> None:
        super().__init__()
        self._gallery_dl_path = gallery_dl_path
        self._queue: deque[DownloadTask] = deque()
        self._current_task: DownloadTask | None = None
        self._process = QProcess(self)
        self._process.readyReadStandardOutput.connect(self._handle_stdout)
        self._process.readyReadStandardError.connect(self._handle_stderr)
        self._process.finished.connect(self._handle_finished)
        self._process.errorOccurred.connect(self._handle_process_error)

    def set_gallery_dl_path(self, gallery_dl_path: str) -> None:
        self._gallery_dl_path = gallery_dl_path

    def enqueue(self, tasks: list[DownloadTask]) -> None:
        for task in tasks:
            task.status = TaskStatus.QUEUED
            task.progress_text = "\u041e\u0436\u0438\u0434\u0430\u043d\u0438\u0435"
            self._queue.append(task)
            self.task_changed.emit(task)
        self.queue_state_changed.emit(bool(self._queue or self._current_task))
        self._start_next_if_needed()

    def stop_current(self) -> None:
        if self._current_task is None:
            return
        self._current_task.status = TaskStatus.CANCELLED
        self._current_task.progress_text = "\u041e\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e"
        self._current_task.last_message = "\u0417\u0430\u0434\u0430\u0447\u0430 \u043e\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0430 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0435\u043c."
        self.task_changed.emit(self._current_task)
        self._process.kill()

    def _start_next_if_needed(self) -> None:
        if self._current_task is not None or not self._queue:
            return

        task = self._queue.popleft()
        task.status = TaskStatus.RUNNING
        task.progress_text = "\u041f\u043e\u0434\u0433\u043e\u0442\u043e\u0432\u043a\u0430"
        self._current_task = task
        self.task_changed.emit(task)
        self.current_task_changed.emit(task)

        command = self._resolve_command()
        if command is None:
            task.status = TaskStatus.ERROR
            task.progress_text = "\u041e\u0448\u0438\u0431\u043a\u0430"
            task.last_message = (
                f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043d\u0430\u0439\u0442\u0438 gallery-dl \u043f\u043e \u043f\u0443\u0442\u0438 '{self._gallery_dl_path}'. "
                "\u041f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u043f\u0440\u043e\u0432\u0435\u0440\u0438\u043b\u043e PATH, \u043b\u043e\u043a\u0430\u043b\u044c\u043d\u044b\u0439 .venv \u0438 \u0442\u0435\u043a\u0443\u0449\u0438\u0439 Python."
            )
            self.task_output.emit(task.id, task.last_message, "stderr")
            self.task_changed.emit(task)
            self._finish_task()
            return

        task_args = self._build_arguments(task)
        arguments = command.prefix_args + task_args
        self.task_output.emit(
            task.id,
            f"> {command.display} {' '.join(shlex.quote(arg) for arg in task_args)}",
            "meta",
        )
        self._process.setProgram(command.program)
        self._process.setArguments(arguments)
        self._process.start()

    def _resolve_command(self) -> ResolvedCommand | None:
        candidate = self._gallery_dl_path.strip()

        if candidate:
            if resolved := self._resolve_explicit_command(candidate):
                return resolved

        if resolved := self._resolve_local_executable():
            return resolved

        if resolved := self._resolve_path_executable():
            return resolved

        if resolved := self._resolve_python_module():
            return resolved

        return None

    def _resolve_explicit_command(self, candidate: str) -> ResolvedCommand | None:
        parts = shlex.split(candidate, posix=False)
        if not parts:
            return None
        program = parts[0]
        if Path(program).exists():
            return ResolvedCommand(program=str(Path(program)), prefix_args=parts[1:], display=candidate)
        executable = shutil.which(program)
        if executable:
            return ResolvedCommand(program=executable, prefix_args=parts[1:], display=candidate)
        return None

    def _resolve_local_executable(self) -> ResolvedCommand | None:
        roots = [
            Path.cwd(),
            Path(__file__).resolve().parents[2],
            Path(sys.executable).resolve().parent.parent,
        ]
        checked: set[Path] = set()
        for root in roots:
            if root in checked:
                continue
            checked.add(root)
            candidate = root / ".venv" / "Scripts" / "gallery-dl.exe"
            if candidate.exists():
                return ResolvedCommand(program=str(candidate), prefix_args=[], display=str(candidate))
        return None

    def _resolve_path_executable(self) -> ResolvedCommand | None:
        executable = shutil.which("gallery-dl")
        if executable:
            return ResolvedCommand(program=executable, prefix_args=[], display=executable)
        return None

    def _resolve_python_module(self) -> ResolvedCommand | None:
        if importlib.util.find_spec("gallery_dl") is None:
            return None
        return ResolvedCommand(
            program=sys.executable,
            prefix_args=["-m", "gallery_dl"],
            display=f"{sys.executable} -m gallery_dl",
        )

    def _build_arguments(self, task: DownloadTask) -> list[str]:
        args = ["--no-colors"]
        opts = task.options

        if task.mode is TaskMode.CHECK:
            args.append("--simulate")

        if opts.organize_by_site:
            args.extend(["-d", opts.destination])
        else:
            args.extend(["-D", opts.destination])

        if not opts.only_new:
            args.append("--no-skip")

        if opts.range_text.strip():
            args.extend(["--range", opts.range_text.strip()])

        if opts.date_after.strip():
            args.extend(["--date-after", opts.date_after.strip()])

        if opts.username.strip():
            args.extend(["-u", opts.username.strip()])
        if opts.password.strip():
            args.extend(["-p", opts.password])
        if opts.cookies_file.strip():
            args.extend(["-C", opts.cookies_file.strip()])
        if opts.cookies_from_browser.strip():
            args.extend(["--cookies-from-browser", opts.cookies_from_browser.strip()])

        if opts.filename_template.strip():
            args.extend(["-f", opts.filename_template.strip()])

        if opts.write_metadata:
            args.append("--write-metadata")
        if opts.write_info_json:
            args.append("--write-info-json")
        if opts.write_tags:
            args.append("--write-tags")

        if opts.archive_format == "zip":
            args.append("--zip")
        elif opts.archive_format == "cbz":
            args.append("--cbz")

        if opts.ugoira_format != "none":
            args.extend(["--ugoira", opts.ugoira_format])

        if opts.proxy_url.strip():
            args.extend(["--proxy", opts.proxy_url.strip()])
        if opts.retries.strip():
            args.extend(["-R", opts.retries.strip()])
        if opts.timeout.strip():
            args.extend(["--http-timeout", opts.timeout.strip()])

        media_filter = self._build_media_filter(opts.media_scope)
        if media_filter:
            args.extend(["--filter", media_filter])

        args.append(task.url)
        return args

    def _build_media_filter(self, media_scope: MediaScope) -> str:
        if media_scope is MediaScope.IMAGES:
            quoted = ", ".join(f"'{item}'" for item in IMAGE_EXTENSIONS)
            return f"extension and extension.lower() in ({quoted})"
        if media_scope is MediaScope.VIDEOS:
            quoted = ", ".join(f"'{item}'" for item in VIDEO_EXTENSIONS)
            return f"extension and extension.lower() in ({quoted})"
        return ""

    def _handle_stdout(self) -> None:
        if self._current_task is None:
            return
        text = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            clean = line.rstrip()
            if not clean:
                continue
            self._current_task.last_message = clean
            self._current_task.progress_text = "\u0412\u044b\u043f\u043e\u043b\u043d\u044f\u0435\u0442\u0441\u044f"
            self.task_output.emit(self._current_task.id, clean, "stdout")
            self.task_changed.emit(self._current_task)

    def _handle_stderr(self) -> None:
        if self._current_task is None:
            return
        text = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            clean = line.rstrip()
            if not clean:
                continue
            self._current_task.last_message = clean
            self._current_task.progress_text = "\u0412\u044b\u043f\u043e\u043b\u043d\u044f\u0435\u0442\u0441\u044f"
            self.task_output.emit(self._current_task.id, clean, "stderr")
            self.task_changed.emit(self._current_task)

    def _handle_process_error(self, error: QProcess.ProcessError) -> None:
        if self._current_task is None:
            return
        self._current_task.status = TaskStatus.ERROR
        self._current_task.progress_text = "\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u043f\u0443\u0441\u043a\u0430"
        self._current_task.last_message = f"QProcess error: {error}"
        self.task_output.emit(self._current_task.id, self._current_task.last_message, "stderr")
        self.task_changed.emit(self._current_task)

    def _handle_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if self._current_task is None:
            return
        task = self._current_task
        task.exit_code = exit_code

        if task.status is TaskStatus.CANCELLED:
            task.last_message = "\u0417\u0430\u0434\u0430\u0447\u0430 \u043e\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u0430."
        elif exit_code == 0:
            task.status = TaskStatus.SUCCESS
            task.progress_text = "\u0413\u043e\u0442\u043e\u0432\u043e"
            if not task.last_message:
                task.last_message = "\u041a\u043e\u043c\u0430\u043d\u0434\u0430 \u0437\u0430\u0432\u0435\u0440\u0448\u0438\u043b\u0430\u0441\u044c \u0443\u0441\u043f\u0435\u0448\u043d\u043e."
        else:
            task.status = TaskStatus.ERROR
            task.progress_text = "\u041e\u0448\u0438\u0431\u043a\u0430"
            if not task.last_message:
                task.last_message = f"gallery-dl \u0437\u0430\u0432\u0435\u0440\u0448\u0438\u043b\u0441\u044f \u0441 \u043a\u043e\u0434\u043e\u043c {exit_code}."

        self.task_changed.emit(task)
        self._finish_task()

    def _finish_task(self) -> None:
        self._current_task = None
        self.current_task_changed.emit(None)
        self.queue_state_changed.emit(bool(self._queue))
        self._start_next_if_needed()
