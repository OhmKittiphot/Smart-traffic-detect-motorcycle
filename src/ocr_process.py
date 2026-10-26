import multiprocessing as mp
import queue
import time

from ocr import ocr_worker

# First launch can be slow while PaddleOCR downloads/loads its models
OCR_STARTUP_TIMEOUT_S = 120.0


class OcrProcess:
    """The plate OCR worker, run in its own process so PaddleOCR does not share the GIL with detection.
    Plate crops go in through in_q; reads come back through out_q (see ocr.ocr_worker)."""

    def __init__(self, model_path: str):
        self.in_q = mp.Queue(maxsize=2)
        self.out_q = mp.Queue(maxsize=2)
        self._proc = mp.Process(target=ocr_worker, args=(self.in_q, self.out_q, model_path), daemon=True)
        self._started = False

    def start(self) -> None:
        """Start the process and block until it reports ready; raise if it failed or died."""
        self._proc.start()
        self._started = True
        print("Waiting for OCR Process...")
        deadline = time.time() + OCR_STARTUP_TIMEOUT_S
        while time.time() < deadline:
            try:
                msg = self.out_q.get(timeout=1.0)
            except queue.Empty:
                if not self._proc.is_alive():
                    raise RuntimeError(f"OCR process exited during startup (exit code {self._proc.exitcode})")
                continue
            if isinstance(msg, dict) and "error" in msg:
                raise RuntimeError(msg["error"])
            print(f"OCR Process Status: {msg}")
            return
        # First run may still be downloading PaddleOCR models; keep going and let results arrive later
        print(f"[OCR] Not ready after {OCR_STARTUP_TIMEOUT_S:.0f}s; continuing while it loads.")

    def problem(self):
        """Why OCR stopped working, or None while it is fine (or not started yet)."""
        if self._started and not self._proc.is_alive():
            return f"OCR process exited (code {self._proc.exitcode})"
        return None

    def release_queues(self) -> None:
        """Ctrl+C also kills the OCR process; without this, exit waits forever to flush queued crops
        into its closed pipe and the program never ends."""
        for q in (self.in_q, self.out_q):
            q.cancel_join_thread()

    def stop(self) -> None:
        if not self._proc.is_alive():
            return
        try:
            self.in_q.put(None, timeout=1)
        except Exception:
            pass
        self._proc.join(timeout=3)
        if self._proc.is_alive():
            print("[OCR] Process did not exit in time; terminating.")
            self._proc.terminate()
            self._proc.join(timeout=2)
