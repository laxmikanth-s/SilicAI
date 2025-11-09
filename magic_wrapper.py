import subprocess
import threading
import logging
import queue
import time

class MagicWrapperError(Exception):
    """Base exception for MagicWrapper errors."""
    pass

class MagicTimeoutError(MagicWrapperError):
    """Exception raised on command timeout."""
    pass

class MagicCommandError(MagicWrapperError):
    """Exception raised on Magic-reported errors."""
    pass

class MagicWrapper:
    """
    A robust Python wrapper for the Magic VLSI layout tool.
    Handles command execution, output capture, timeouts, and error reporting.
    """
    def __init__(self, magic_path='magic', startup_timeout=10, log_file=None):
        """
        Initialize MagicWrapper.
        
        :param magic_path: Path to the Magic EDA executable.
        :param startup_timeout: Timeout (seconds) for Magic process startup.
        :param log_file: Optional file path to log commands and outputs.
        """
        self.magic_path = magic_path
        self.process = None
        self.lock = threading.Lock()
        self.log_file = log_file
        self.startup_timeout = startup_timeout
        self._start_process()

    def _start_process(self):
        """Start the Magic process."""
        try:
            self.process = subprocess.Popen(
                [self.magic_path, "-noconsole"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1  # line-buffered
            )
            # Allow startup to settle (optional, can be adjusted/removed)
            time.sleep(0.2)
        except FileNotFoundError as e:
            raise MagicWrapperError(f"Magic executable not found: {e}")
        except Exception as e:
            raise MagicWrapperError(f"Error starting Magic: {e}")

    def _read_output(self, out_queue, err_queue):
        """Internal thread target for non-blocking read."""
        try:
            stdout_line = self.process.stdout.readline()
            stderr_line = self.process.stderr.readline()
            out_queue.put(stdout_line)
            err_queue.put(stderr_line)
        except Exception as e:
            err_queue.put(f"Error reading output: {e}")

    def send_command(self, command, timeout=15):
        """
        Send a command to Magic and capture response.

        :param command: Command string to send.
        :param timeout: Time (secs) to wait for response.
        :return: Output string from Magic.
        :raises MagicTimeoutError: On timeout.
        :raises MagicCommandError: On Magic reported errors.
        :raises MagicWrapperError: On other errors (including process death).
        """
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                raise MagicWrapperError("Magic process is not running or already closed.")

            try:
                # Send command
                self.process.stdin.write(command + "\n")
                self.process.stdin.flush()

                out_queue = queue.Queue()
                err_queue = queue.Queue()
                reader_thread = threading.Thread(
                    target=self._read_output,
                    args=(out_queue, err_queue)
                )
                reader_thread.start()
                reader_thread.join(timeout)

                if reader_thread.is_alive():
                    self.process.kill()
                    reader_thread.join()
                    raise MagicTimeoutError(f"Timeout executing command: {command}")

                stdout = out_queue.get() if not out_queue.empty() else ""
                stderr = err_queue.get() if not err_queue.empty() else ""

                if self.log_file:
                    with open(self.log_file, "a") as logf:
                        logf.write(f"Command: {command}\nOutput: {stdout}\nError: {stderr}\n---\n")

                if stderr.strip():
                    raise MagicCommandError(f"Magic error: {stderr.strip()}")

                return stdout.strip()

            except (OSError, subprocess.SubprocessError) as e:
                raise MagicWrapperError(f"OS error during command: {e}")
            except UnicodeDecodeError as e:
                raise MagicWrapperError(f"Unicode decode error: {e}")

    def close(self):
        """Cleanly shut down the Magic process."""
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.write('quit\n')
                self.process.stdin.flush()
                self.process.terminate()
            except Exception as e:
                logging.warning(f"Error closing Magic process: {e}")
        self.process = None

    def __del__(self):
        self.close()

# Example usage:
# try:
#     mw = MagicWrapper()
#     print(mw.send_command("tech load scmos"))
#     mw.close()
# except MagicWrapperError as err:
#     print(f"Error: {err}")
