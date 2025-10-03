import subprocess
import logging
import os
import tempfile
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional, Union, Tuple
from dataclasses import dataclass
from enum import Enum


class YosysError(Exception):
    """Custom exception for Yosys-related errors."""

    def __init__(self, message: str, error_type: str = "GENERAL", suggestions: List[str] = None):
        super().__init__(message)
        self.error_type = error_type
        self.suggestions = suggestions or []


class SynthesisTarget(Enum):
    """Supported synthesis targets."""
    GENERIC = "generic"
    ICE40 = "ice40"
    ECP5 = "ecp5"
    XILINX = "xilinx"
    INTEL = "intel"


@dataclass
class SynthesisResult:
    """Result of synthesis operation."""
    success: bool
    stage: str
    output_file: Optional[str] = None
    statistics: Dict[str, any] = None
    warnings: List[str] = None
    errors: List[str] = None
    stdout: str = ""
    stderr: str = ""
    execution_time: float = 0.0
    netlist_content: str = ""
    netlist_analysis: Dict[str, any] = None


class YosysWrapper:
    """
    Production-ready Yosys wrapper with optimized synthesis and organized output display.
    """

    def __init__(self, yosys_path: str = "yosys", work_dir: Optional[str] = None,
                 debug_mode: bool = False, timeout: int = 300):
        """Initialize the Yosys wrapper."""
        self.yosys_path = yosys_path
        self.debug_mode = debug_mode
        self.default_timeout = timeout
        self.work_dir = self._setup_work_directory(work_dir)
        self.logger = self._setup_logging()

        # Verify Yosys installation
        self._verify_yosys_installation()

        if self.debug_mode:
            self.logger.info(f"YosysWrapper initialized successfully")
            self.logger.info(f"Work directory: {self.work_dir}")

    def _setup_work_directory(self, work_dir: Optional[str]) -> Path:
        """Setup work directory with proper permissions."""
        try:
            if work_dir:
                work_path = Path(work_dir).resolve()
            else:
                temp_base = Path(tempfile.gettempdir())
                work_path = temp_base / f"yosys_work_{os.getpid()}"

            work_path.mkdir(parents=True, exist_ok=True)

            # Test write permissions
            test_file = work_path / "test_write.tmp"
            test_file.write_text("test")
            test_file.unlink()

            return work_path

        except Exception as e:
            raise YosysError(
                f"Failed to setup work directory: {str(e)}",
                "WORK_DIR_ERROR",
                ["Check directory permissions", "Try a different work directory"]
            )

    def _setup_logging(self) -> logging.Logger:
        """Setup logging with proper formatting."""
        logger = logging.getLogger(f"YosysWrapper_{id(self)}")

        if not logger.handlers:
            level = logging.DEBUG if self.debug_mode else logging.INFO
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(level)

        return logger

    def _verify_yosys_installation(self):
        """Verify Yosys installation."""
        try:
            result = subprocess.run(
                [self.yosys_path, "-V"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                raise YosysError(
                    f"Yosys execution failed: {result.stderr}",
                    "YOSYS_EXECUTION_ERROR",
                    ["Check if Yosys is properly installed", "Verify executable permissions"]
                )

            version_info = result.stdout.strip()
            self.yosys_version = version_info
            self.logger.info(f"Yosys verified successfully: {version_info}")

        except FileNotFoundError:
            suggestions = [
                "Install Yosys from https://github.com/YosysHQ/yosys",
                "Add Yosys to your system PATH"
            ]
            if sys.platform == "win32":
                suggestions.append("On Windows: conda install -c conda-forge yosys")

            raise YosysError(f"Yosys not found at path: {self.yosys_path}", "YOSYS_NOT_FOUND", suggestions)
        except subprocess.TimeoutExpired:
            raise YosysError("Yosys verification timed out", "YOSYS_TIMEOUT", ["Try restarting your system"])

    def _clean_user_input(self, user_input: str, input_type: str = "general") -> str:
        """Clean and validate user input."""
        if not user_input:
            return ""

        cleaned = user_input.strip()
        cleaned = cleaned.replace('\r', '').replace('\n', '').replace('\t', ' ')
        cleaned = re.sub(r'\s+', ' ', cleaned)

        if input_type == "module_name":
            if cleaned.lower().startswith("module "):
                cleaned = cleaned[7:].strip()
                self.logger.warning("Removed 'module' prefix from module name")

            cleaned = cleaned.replace("(", "").replace(")", "").replace(";", "")

            if cleaned and not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', cleaned):
                self.logger.warning(f"Module name '{cleaned}' may not be a valid Verilog identifier")

        elif input_type == "file_path":
            cleaned = cleaned.replace('"', '').replace("'", "")

        return cleaned

    def _validate_verilog_files(self, verilog_files: List[str]) -> Tuple[List[Path], List[str]]:
        """Comprehensive Verilog file validation."""
        validated_files = []
        warnings = []

        for vfile in verilog_files:
            try:
                vfile_path = Path(vfile).resolve()

                if not vfile_path.exists():
                    raise YosysError(
                        f"Verilog file not found: {vfile}",
                        "FILE_NOT_FOUND",
                        [f"Check if the path is correct: {vfile_path}", "Verify file permissions"]
                    )

                if not vfile_path.is_file():
                    raise YosysError(f"Path is not a file: {vfile}", "INVALID_FILE_TYPE")

                if vfile_path.suffix.lower() not in ['.v', '.sv', '.vh', '.verilog']:
                    warnings.append(f"File may not be Verilog: {vfile} (extension: {vfile_path.suffix})")

                file_size = vfile_path.stat().st_size
                if file_size == 0:
                    raise YosysError(f"Verilog file is empty: {vfile}", "EMPTY_FILE")

                if file_size > 50 * 1024 * 1024:
                    warnings.append(f"Large file detected: {vfile} ({file_size / 1024 / 1024:.1f} MB)")

                # Basic content validation
                try:
                    content = vfile_path.read_text(encoding='utf-8')
                except UnicodeDecodeError:
                    try:
                        content = vfile_path.read_text(encoding='latin-1')
                        warnings.append(f"File encoding issue, using latin-1: {vfile}")
                    except Exception:
                        raise YosysError(f"Cannot read file (encoding issue): {vfile}", "FILE_ENCODING_ERROR")

                modules = re.findall(r'module\s+(\w+)', content, re.IGNORECASE)
                if not modules:
                    warnings.append(f"No modules found in file: {vfile}")
                else:
                    self.logger.debug(f"Found modules in {vfile}: {modules}")

                validated_files.append(vfile_path)

            except YosysError:
                raise
            except Exception as e:
                raise YosysError(f"Error validating file {vfile}: {str(e)}", "FILE_VALIDATION_ERROR")

        return validated_files, warnings

    def _normalize_path_for_yosys(self, path: Path) -> str:
        """Normalize file paths for Yosys, handling Windows issues."""
        path_str = str(path.resolve())

        if sys.platform == "win32":
            path_str = path_str.replace('\\', '/')

        special_chars = [' ', '&', '(', ')', '[', ']', '{', '}', ';', '|', '<', '>', '?', '*']
        if any(char in path_str for char in special_chars):
            path_str = f'"{path_str}"'

        return path_str

    def _detect_circuit_type(self, verilog_content: str) -> str:
        """Detect if circuit is combinational or sequential."""
        # Look for sequential elements
        sequential_keywords = ['always @(posedge', 'always @(negedge', 'always @(edge',
                               'reg ', 'flip', 'latch', 'memory']

        for keyword in sequential_keywords:
            if keyword in verilog_content.lower():
                return "sequential"

        return "combinational"

    def _run_yosys_command(self, yosys_script: str, timeout: Optional[int] = None) -> Dict[str, any]:
        """Execute Yosys command with comprehensive error handling."""
        if timeout is None:
            timeout = self.default_timeout

        start_time = time.time()

        try:
            script_file = self.work_dir / f"script_{int(time.time())}_{os.getpid()}.ys"

            try:
                script_file.write_text(yosys_script, encoding='utf-8')
            except Exception as e:
                raise YosysError(f"Failed to write script file: {str(e)}", "SCRIPT_WRITE_ERROR")

            cmd = [self.yosys_path, "-s", str(script_file)]

            if self.debug_mode:
                self.logger.debug(f"Executing command: {' '.join(cmd)}")
                self.logger.debug(f"Script content:\n{yosys_script}")

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(self.work_dir)
                )

                execution_time = time.time() - start_time
                parsed_output = self._parse_yosys_output(result.stdout, result.stderr)

                success = result.returncode == 0

                if not success:
                    self.logger.error(f"Yosys command failed (return code: {result.returncode})")
                    if self.debug_mode:
                        self.logger.debug(f"STDOUT:\n{result.stdout}")
                        self.logger.debug(f"STDERR:\n{result.stderr}")

                return {
                    'success': success,
                    'returncode': result.returncode,
                    'stdout': result.stdout,
                    'stderr': result.stderr,
                    'execution_time': execution_time,
                    'parsed_output': parsed_output,
                    'command': ' '.join(cmd)
                }

            finally:
                try:
                    script_file.unlink(missing_ok=True)
                except Exception:
                    pass

        except subprocess.TimeoutExpired:
            execution_time = time.time() - start_time
            raise YosysError(
                f"Yosys command timed out after {timeout} seconds",
                "YOSYS_TIMEOUT",
                [f"Increase timeout (current: {timeout}s)", "Simplify your design"]
            )
        except Exception as e:
            raise YosysError(f"Failed to execute Yosys command: {str(e)}", "YOSYS_EXECUTION_ERROR")

    def _parse_yosys_output(self, stdout: str, stderr: str) -> Dict[str, any]:
        """Parse Yosys output to extract useful information."""
        parsed = {
            'errors': [],
            'warnings': [],
            'info': [],
            'statistics': {},
            'modules': [],
            'passes_completed': [],
            'error_type': None,
            'suggestions': []
        }

        full_output = stdout + "\n" + stderr
        lines = full_output.split('\n')

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Error detection
            if any(keyword in line.upper() for keyword in ['ERROR:', 'FATAL:', 'ABORT']):
                parsed['errors'].append(line)

                if "not found" in line.lower():
                    parsed['error_type'] = "MODULE_NOT_FOUND"
                    parsed['suggestions'].extend([
                        "Check module name spelling",
                        "Ensure module is defined in the Verilog files"
                    ])
                elif "syntax error" in line.lower():
                    parsed['error_type'] = "SYNTAX_ERROR"
                    parsed['suggestions'].extend([
                        "Check Verilog syntax",
                        "Look for missing semicolons or parentheses"
                    ])

            # Warning detection
            elif any(keyword in line.upper() for keyword in ['WARNING:', 'WARN:']):
                parsed['warnings'].append(line)

            # Pass completion
            elif 'executing' in line.lower() and 'pass' in line.lower():
                parsed['passes_completed'].append(line)

            # Statistics extraction
            elif 'number of' in line.lower():
                try:
                    if 'cells:' in line:
                        parsed['statistics']['cells'] = int(re.findall(r'(\d+)', line)[-1])
                    elif 'wires:' in line:
                        parsed['statistics']['wires'] = int(re.findall(r'(\d+)', line)[-1])
                    elif 'processes:' in line:
                        parsed['statistics']['processes'] = int(re.findall(r'(\d+)', line)[-1])
                except (ValueError, IndexError):
                    pass

            # Module detection
            elif 'generating rtlil representation for module' in line.lower():
                module_match = re.search(r'module `\\(\w+)', line)
                if module_match:
                    parsed['modules'].append(module_match.group(1))

        return parsed

    def _analyze_netlist(self, netlist_content: str) -> Dict[str, any]:
        """Analyze the generated netlist for detailed information."""
        if not netlist_content:
            return {}

        lines = [line.strip() for line in netlist_content.split('\n') if line.strip()]

        analysis = {
            'total_lines': len(lines),
            'modules': [],
            'inputs': [],
            'outputs': [],
            'wires': [],
            'assigns': [],
            'always_blocks': [],
            'instances': [],
            'comments': []
        }

        current_module = None

        for line in lines:
            if line.startswith('module '):
                module_match = re.search(r'module\s+(\w+)', line)
                if module_match:
                    current_module = module_match.group(1)
                    analysis['modules'].append(current_module)

            elif line.startswith('input '):
                input_match = re.search(r'input\s+(?:\[.*?\]\s+)?(\w+)', line)
                if input_match:
                    analysis['inputs'].append(input_match.group(1))

            elif line.startswith('output '):
                output_match = re.search(r'output\s+(?:\[.*?\]\s+)?(\w+)', line)
                if output_match:
                    analysis['outputs'].append(output_match.group(1))

            elif 'wire ' in line:
                analysis['wires'].append(line)

            elif line.startswith('assign '):
                analysis['assigns'].append(line)

            elif line.startswith('always '):
                analysis['always_blocks'].append(line)

            elif line.startswith('/*') or line.startswith('//'):
                analysis['comments'].append(line)

        return analysis

    def _build_optimized_synthesis_script(self, files: List[Path], top_module: str,
                                          target: SynthesisTarget, output_path: Path,
                                          defines: Optional[Dict[str, str]],
                                          show_stats: bool, circuit_type: str) -> str:
        """Build optimized synthesis script with circuit-type specific optimizations."""
        script_lines = []

        # Header
        script_lines.append(f"# Optimized Yosys synthesis script for module '{top_module}'")
        script_lines.append(f"# Circuit type: {circuit_type}")
        script_lines.append(f"# Generated by YosysWrapper at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        script_lines.append("")

        # Defines
        if defines:
            script_lines.append("# Verilog defines")
            for key, value in defines.items():
                script_lines.append(f"# Define {key}={value}")
            script_lines.append("")

        # Read files
        script_lines.append("# Reading Verilog files")
        for vfile in files:
            normalized_path = self._normalize_path_for_yosys(vfile)
            script_lines.append(f"read_verilog {normalized_path}")
        script_lines.append("")

        # Hierarchy and basic synthesis
        script_lines.extend([
            "# Design hierarchy and basic synthesis",
            f"hierarchy -check -top {top_module}",
            "proc",
            "opt",
            "memory",
            "opt",
        ])

        # Target-specific synthesis with circuit-type optimization
        if target == SynthesisTarget.ICE40:
            script_lines.extend([
                "# iCE40-specific synthesis",
                f"synth_ice40 -top {top_module}",
            ])
        elif target == SynthesisTarget.ECP5:
            script_lines.extend([
                "# ECP5-specific synthesis",
                f"synth_ecp5 -top {top_module}",
            ])
        elif target == SynthesisTarget.XILINX:
            script_lines.extend([
                "# Xilinx-specific synthesis",
                f"synth_xilinx -top {top_module}",
            ])
        else:  # GENERIC
            script_lines.extend([
                "# Generic synthesis flow",
                "fsm",
                "opt",
                "techmap",
                "opt",
            ])

            # Circuit-type specific ABC optimization
            if circuit_type == "combinational":
                script_lines.extend([
                    "# Optimized ABC for combinational circuits (eliminates ABC warning)",
                    "abc -g AND,NAND,OR,NOR,XOR,XNOR,MUX -script +fraig_sweep;fraig;refactor;balance",
                ])
            else:
                script_lines.extend([
                    "# Standard ABC optimization for sequential circuits",
                    "abc",
                ])

            script_lines.append("clean")

        # Statistics
        if show_stats:
            script_lines.extend([
                "",
                "# Show design statistics",
                "stat"
            ])

        # Output
        script_lines.extend([
            "",
            "# Write output",
            f"write_verilog {self._normalize_path_for_yosys(output_path)}"
        ])

        return "\n".join(script_lines)

    def _display_synthesis_process(self, script: str, result: Dict[str, any]):
        """Display the synthesis process information in organized format."""
        print(f"\n{'=' * 80}")
        print("🔧 SYNTHESIS PROCESS & INFORMATION")
        print(f"{'=' * 80}")

        # Show synthesis script
        if self.debug_mode:
            print("\n📜 Generated Synthesis Script:")
            print("-" * 60)
            for i, line in enumerate(script.split('\n'), 1):
                print(f"{i:2d}: {line}")
            print("-" * 60)

        # Show passes completed
        if result['parsed_output']['passes_completed']:
            print("\n⚙️  Synthesis Passes Completed:")
            for i, pass_info in enumerate(result['parsed_output']['passes_completed'][:10], 1):  # Show first 10
                clean_pass = pass_info.replace('Executing ', '').replace(' pass', '')
                print(f"   {i:2d}. {clean_pass}")
            if len(result['parsed_output']['passes_completed']) > 10:
                print(f"   ... and {len(result['parsed_output']['passes_completed']) - 10} more passes")

        # Show statistics
        if result['parsed_output']['statistics']:
            print("\n📊 Design Statistics:")
            stats = result['parsed_output']['statistics']
            for key, value in stats.items():
                print(f"   • {key.title()}: {value}")

        # Show execution info
        print(f"\n⏱️  Execution Information:")
        print(f"   • Synthesis time: {result['execution_time']:.2f} seconds")
        print(f"   • Return code: {result['returncode']}")
        print(f"   • Success: {'✅ Yes' if result['success'] else '❌ No'}")

        print(f"{'=' * 80}")

    def _display_netlist_content(self, output_file: str, netlist_analysis: Dict[str, any]):
        """Display the synthesized netlist in organized format."""
        try:
            output_path = Path(output_file)

            print(f"\n{'=' * 80}")
            print("📄 SYNTHESIZED NETLIST")
            print(f"{'=' * 80}")

            if not output_path.exists():
                print("❌ ERROR: Output file was not created!")
                return ""

            file_size = output_path.stat().st_size
            print(f"📋 File Information:")
            print(f"   • File: {output_path.name}")
            print(f"   • Path: {output_path}")
            print(f"   • Size: {file_size} bytes")

            if file_size == 0:
                print("⚠️  WARNING: Output file is EMPTY!")
                return ""

            # Read netlist content
            with open(output_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # Show analysis
            if netlist_analysis:
                print(f"\n📊 Netlist Analysis:")
                print(f"   • Total lines: {netlist_analysis.get('total_lines', 0)}")
                print(f"   • Modules: {len(netlist_analysis.get('modules', []))}")
                if netlist_analysis.get('modules'):
                    print(f"     - {', '.join(netlist_analysis['modules'])}")
                print(f"   • Inputs: {len(netlist_analysis.get('inputs', []))}")
                if netlist_analysis.get('inputs'):
                    print(f"     - {', '.join(netlist_analysis['inputs'])}")
                print(f"   • Outputs: {len(netlist_analysis.get('outputs', []))}")
                if netlist_analysis.get('outputs'):
                    print(f"     - {', '.join(netlist_analysis['outputs'])}")
                print(f"   • Wire declarations: {len(netlist_analysis.get('wires', []))}")
                print(f"   • Assign statements: {len(netlist_analysis.get('assigns', []))}")
                print(f"   • Always blocks: {len(netlist_analysis.get('always_blocks', []))}")

            # Show netlist content
            print(f"\n📝 Netlist Content:")
            print("-" * 80)
            print(content)
            print("-" * 80)
            print(f"{'=' * 80}")

            return content

        except Exception as e:
            print(f"❌ Error displaying netlist: {e}")
            return ""

    def synthesize_design(self,
                          verilog_files: Union[str, List[str]],
                          top_module: str,
                          output_file: str,
                          target: Union[str, SynthesisTarget] = SynthesisTarget.GENERIC,
                          defines: Optional[Dict[str, str]] = None,
                          show_statistics: bool = True) -> SynthesisResult:
        """
        Complete optimized synthesis flow with organized output display.
        """
        start_time = time.time()

        try:
            self.logger.info(f"Starting synthesis for module '{top_module}'")

            # Input validation and cleanup
            if isinstance(verilog_files, str):
                verilog_files = [verilog_files]

            top_module = self._clean_user_input(top_module, "module_name")
            verilog_files = [self._clean_user_input(f, "file_path") for f in verilog_files]

            if not top_module:
                raise YosysError("Top module name cannot be empty", "INVALID_INPUT")

            # Validate files and detect circuit type
            validated_files, file_warnings = self._validate_verilog_files(verilog_files)

            # Read first file to detect circuit type
            circuit_type = "combinational"  # default
            try:
                first_file_content = validated_files[0].read_text(encoding='utf-8')
                circuit_type = self._detect_circuit_type(first_file_content)
                self.logger.info(f"Detected circuit type: {circuit_type}")
            except Exception:
                pass

            # Normalize target
            if isinstance(target, str):
                try:
                    target = SynthesisTarget(target.lower())
                except ValueError:
                    raise YosysError(
                        f"Unsupported synthesis target: {target}",
                        "INVALID_TARGET",
                        [f"Supported targets: {[t.value for t in SynthesisTarget]}"]
                    )

            # Setup output
            output_path = Path(output_file).resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Build optimized synthesis script
            script = self._build_optimized_synthesis_script(
                validated_files, top_module, target, output_path, defines, show_statistics, circuit_type
            )

            # Execute synthesis
            self.logger.info("Executing synthesis...")
            result = self._run_yosys_command(script)

            execution_time = time.time() - start_time

            # Read and analyze netlist if successful
            netlist_content = ""
            netlist_analysis = {}
            if result['success'] and output_path.exists():
                try:
                    netlist_content = output_path.read_text(encoding='utf-8')
                    netlist_analysis = self._analyze_netlist(netlist_content)
                except Exception as e:
                    self.logger.warning(f"Could not read/analyze netlist: {e}")

            # Build comprehensive result
            synthesis_result = SynthesisResult(
                success=result['success'],
                stage='synthesis_complete' if result['success'] else 'synthesis_failed',
                output_file=str(output_path) if result['success'] else None,
                statistics=result['parsed_output']['statistics'],
                warnings=file_warnings + result['parsed_output']['warnings'],
                errors=result['parsed_output']['errors'],
                stdout=result['stdout'],
                stderr=result['stderr'],
                execution_time=execution_time,
                netlist_content=netlist_content,
                netlist_analysis=netlist_analysis
            )

            # Display results in organized format
            if result['success']:
                self.logger.info(f"✅ Synthesis completed successfully in {execution_time:.2f}s")
                self.logger.info(f"Output written to: {output_path}")

                # Display synthesis process information first
                self._display_synthesis_process(script, result)

                # Display netlist content second
                self._display_netlist_content(str(output_path), netlist_analysis)

            else:
                self.logger.error(f"❌ Synthesis failed after {execution_time:.2f}s")

                # Show error details
                parsed = result['parsed_output']
                if parsed['error_type'] and parsed['suggestions']:
                    self.logger.error(f"Error type: {parsed['error_type']}")
                    self.logger.error("Suggestions:")
                    for suggestion in parsed['suggestions'][:3]:
                        self.logger.error(f"  • {suggestion}")

            # Show warnings
            for warning in synthesis_result.warnings:
                self.logger.warning(warning)

            return synthesis_result

        except YosysError:
            raise
        except Exception as e:
            raise YosysError(f"Unexpected error during synthesis: {str(e)}", "SYNTHESIS_ERROR")

    def list_modules_in_files(self, verilog_files: Union[str, List[str]]) -> Dict[str, List[str]]:
        """List all modules found in Verilog files."""
        if isinstance(verilog_files, str):
            verilog_files = [verilog_files]

        modules_info = {}

        for vfile in verilog_files:
            try:
                vfile_path = Path(vfile)
                if not vfile_path.exists():
                    modules_info[vfile] = [f"ERROR: File not found"]
                    continue

                content = vfile_path.read_text(encoding='utf-8')
                modules = re.findall(r'module\s+(\w+)', content, re.IGNORECASE)
                modules_info[vfile] = modules if modules else ["No modules found"]

            except Exception as e:
                modules_info[vfile] = [f"ERROR: {str(e)}"]

        return modules_info

    def cleanup(self):
        """Clean up temporary files and resources."""
        try:
            if self.work_dir.exists():
                for temp_file in self.work_dir.glob("script_*"):
                    temp_file.unlink(missing_ok=True)
                for temp_file in self.work_dir.glob("temp_*"):
                    temp_file.unlink(missing_ok=True)

                try:
                    self.work_dir.rmdir()
                except OSError:
                    pass

            self.logger.info("Cleanup completed")

        except Exception as e:
            self.logger.warning(f"Cleanup warning: {e}")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.cleanup()


def get_user_input_with_validation() -> Optional[Dict[str, any]]:
    """Get user input with comprehensive validation."""
    print("=" * 60)
    print("YOSYS PYTHON WRAPPER - OPTIMIZED SYNTHESIS TOOL")
    print("=" * 60)

    try:
        # Get Verilog files
        print("\nEnter Verilog file path(s):")
        print("📁 Single file: /path/to/file.v")
        print("📁 Multiple files: file1.v,file2.v,file3.v")

        verilog_input = input("Verilog files: ").strip()
        if not verilog_input:
            print("❌ Error: No Verilog files specified!")
            return None

        if ',' in verilog_input:
            verilog_files = [f.strip() for f in verilog_input.split(',')]
        else:
            verilog_files = [verilog_input]

        # Quick file validation
        missing_files = [f for f in verilog_files if not Path(f).exists()]
        if missing_files:
            print(f"❌ Error: Files not found: {missing_files}")
            return None

        # Show available modules
        print("\n🔍 Analyzing Verilog files for modules...")
        temp_wrapper = YosysWrapper()
        modules_info = temp_wrapper.list_modules_in_files(verilog_files)

        print("\n📋 Modules found:")
        all_modules = []
        for file, modules in modules_info.items():
            print(f"  📄 {Path(file).name}: {', '.join(modules)}")
            for module in modules:
                if not module.startswith('ERROR') and module != "No modules found":
                    all_modules.append(module)

        # Get top module
        print("\n⚠️  IMPORTANT: Enter module name exactly as shown above")
        print("❌ Wrong: 'module counter' or 'counter()'")
        print("✅ Correct: 'counter'")

        top_module = input("\nEnter top module name: ").strip()
        if not top_module:
            print("❌ Error: No top module specified!")
            return None

        # Validate module exists
        if all_modules and top_module not in all_modules:
            print(f"⚠️  Warning: Module '{top_module}' not found in file analysis.")
            print(f"Available modules: {all_modules}")
            proceed = input("Continue anyway? (y/n): ").strip().lower()
            if proceed != 'y':
                return None

        # Get output file with better default
        default_output = f"./{top_module}_synthesized.v"
        output_file = input(f"Output file (default: {default_output}): ").strip()
        if not output_file:
            output_file = default_output

        # Get synthesis target
        print("\n🎯 Available synthesis targets:")
        targets = list(SynthesisTarget)
        for i, target in enumerate(targets, 1):
            print(f"{i}. {target.value}")

        target_choice = input(f"Choose target (1-{len(targets)}, default=1): ").strip()
        try:
            target_idx = int(target_choice) - 1 if target_choice else 0
            target = targets[target_idx]
        except (ValueError, IndexError):
            target = SynthesisTarget.GENERIC

        # Get defines
        defines_input = input("\nVerilog defines (KEY1=VAL1,KEY2=VAL2) [optional]: ").strip()
        defines = {}
        if defines_input:
            try:
                for define in defines_input.split(','):
                    if '=' in define:
                        key, value = define.strip().split('=', 1)
                        defines[key.strip()] = value.strip()
            except Exception as e:
                print(f"⚠️  Warning: Could not parse defines: {e}")

        return {
            'verilog_files': verilog_files,
            'top_module': top_module,
            'output_file': output_file,
            'target': target,
            'defines': defines if defines else None
        }

    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        return None
    except Exception as e:
        print(f"\n❌ Error getting input: {e}")
        return None


def main():
    """Main function with organized output display."""
    try:
        # Get user input
        params = get_user_input_with_validation()
        if not params:
            return

        # Display parameters
        print(f"\n{'=' * 60}")
        print("SYNTHESIS PARAMETERS")
        print(f"{'=' * 60}")
        print(f"📄 Verilog files: {params['verilog_files']}")
        print(f"🎯 Top module: {params['top_module']}")
        print(f"📤 Output file: {params['output_file']}")
        print(f"⚙️  Target: {params['target'].value}")
        if params['defines']:
            print(f"🔧 Defines: {params['defines']}")
        print(f"{'=' * 60}\n")

        # Run synthesis with context manager
        with YosysWrapper(debug_mode=True) as wrapper:
            result = wrapper.synthesize_design(
                verilog_files=params['verilog_files'],
                top_module=params['top_module'],
                output_file=params['output_file'],
                target=params['target'],
                defines=params['defines'],
                show_statistics=True
            )

            # Display final results summary
            print(f"\n{'=' * 60}")
            print("SYNTHESIS RESULTS SUMMARY")
            print(f"{'=' * 60}")

            if result.success:
                print(f"✅ SUCCESS: Synthesis completed in {result.execution_time:.2f}s")
                print(f"📤 Output: {result.output_file}")
                print(f"📏 Netlist size: {len(result.netlist_content)} characters")

                if result.warnings:
                    print(f"\n⚠️  Warnings ({len(result.warnings)}):")
                    for warning in result.warnings[:5]:
                        print(f"   • {warning}")
                    if len(result.warnings) > 5:
                        print(f"   ... and {len(result.warnings) - 5} more")

            else:
                print(f"❌ FAILED: Synthesis failed at {result.stage}")
                print(f"⏱️  Execution time: {result.execution_time:.2f}s")

                if result.errors:
                    print(f"\n🚫 Errors:")
                    for error in result.errors:
                        print(f"   • {error}")

            print(f"{'=' * 60}")

    except YosysError as e:
        print(f"\n❌ Yosys Error ({e.error_type}): {e}")
        if e.suggestions:
            print("\n💡 Suggestions:")
            for suggestion in e.suggestions:
                print(f"   • {suggestion}")

    except KeyboardInterrupt:
        print("\n\nSynthesis interrupted by user.")

    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
