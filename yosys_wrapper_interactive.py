

import subprocess
import os
import tempfile
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional, Union
from dataclasses import dataclass
from enum import Enum


class SynthesisTarget(Enum):
    """Supported synthesis targets."""
    GENERIC = "generic"
    ICE40 = "ice40"
    ECP5 = "ecp5"
    XILINX = "xilinx"


@dataclass
class SynthesisResult:
    """Result of synthesis operation."""
    success: bool
    stage: str
    output_file: Optional[str] = None
    execution_time: float = 0.0
    netlist_content: str = ""
    messages: List[str] = None
    
    def __post_init__(self):
        if self.messages is None:
            self.messages = []


class YosysWrapper:
    """
    Ultra-optimized Yosys wrapper with interactive input
    """
    
    def __init__(self, yosys_path: str = "yosys", work_dir: Optional[str] = None):
        """Initialize wrapper."""
        self.yosys_path = yosys_path
        self.work_dir = Path(work_dir) if work_dir else Path(tempfile.gettempdir()) / f"yosys_{os.getpid()}"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        
        # Verify Yosys exists
        try:
            result = subprocess.run(
                [self.yosys_path, "-V"], 
                capture_output=True, 
                timeout=5, 
                text=True
            )
            if result.returncode == 0:
                print(f"✅ Yosys found: {result.stdout.strip()}\n")
        except FileNotFoundError:
            print(f"❌ Yosys not found at: {self.yosys_path}")
            raise
        except Exception as e:
            print(f"❌ Error verifying Yosys: {e}")
            raise
    
    def read_file_safe(self, file_path: str) -> str:
        """Read file safely with multiple encoding attempts."""
        p = Path(file_path).resolve()
        
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        
        # Try encodings
        for encoding in ['utf-8', 'latin-1', 'cp1252']:
            try:
                return p.read_text(encoding=encoding)
            except:
                continue
        
        # Fallback
        return p.read_bytes().decode('utf-8', errors='ignore')
    
    def extract_modules(self, file_path: str) -> List[str]:
        """Extract module names from file."""
        try:
            content = self.read_file_safe(file_path)
            modules = re.findall(r'module\s+(\w+)', content, re.IGNORECASE)
            return modules if modules else []
        except Exception as e:
            print(f"⚠️  Error extracting modules: {e}")
            return []
    
    def find_verilog_files(self, path: str) -> List[Path]:
        """Find all .v files in path or return file itself."""
        p = Path(path).resolve()
        
        if not p.exists():
            raise FileNotFoundError(f"Path not found: {p}")
        
        files = []
        
        if p.is_file():
            # Single file
            if p.suffix.lower() in ['.v', '.sv', '.verilog', '.vh']:
                files.append(p)
            else:
                raise FileNotFoundError(f"Not a Verilog file: {p}")
        else:
            # Directory - find all .v files
            files.extend(p.glob("*.v"))
            files.extend(p.glob("*.sv"))
        
        if not files:
            raise FileNotFoundError(f"No Verilog files found in: {p}")
        
        return files
    
    def normalize_path(self, p: Path) -> str:
        """Normalize path for Yosys."""
        s = str(p.resolve())
        
        # Windows fix - convert backslashes to forward slashes
        if sys.platform == "win32":
            s = s.replace('\\', '/')
        
        # Quote if contains special characters
        if ' ' in s or '&' in s or '(' in s or ')' in s:
            s = f'"{s}"'
        
        return s
    
    def display_modules(self, verilog_files: List[Path]):
        """Display available modules in files."""
        print("📋 Available modules:")
        for vf in verilog_files:
            modules = self.extract_modules(str(vf))
            if modules:
                print(f"   📄 {vf.name}: {', '.join(modules)}")
            else:
                print(f"   📄 {vf.name}: (no modules detected)")
        print()
    
    def get_user_input(self) -> tuple:
        """Get user input for synthesis."""
        print("="*70)
        print("YOSYS PYTHON WRAPPER - OPTIMIZED SYNTHESIS TOOL")
        print("="*70)
        print()
        
        # Get input file/directory
        while True:
            print("📁 Enter Verilog file or directory path:")
            print("   Example: D:\\langG\\4_bitcount.v")
            print("   Example: D:\\langG\\designs")
            input_path = input("Path: ").strip()
            
            if not input_path:
                print("❌ Error: No path provided!")
                print()
                continue
            
            try:
                verilog_files = self.find_verilog_files(input_path)
                print(f"✅ Found {len(verilog_files)} Verilog file(s)\n")
                break
            except Exception as e:
                print(f"❌ Error: {e}\n")
                continue
        
        # Display available modules
        self.display_modules(verilog_files)
        
        # Get top module
        while True:
            print("🎯 Enter top module name (press Enter to auto-detect):")
            top_module = input("Top module: ").strip() or None
            
            if not top_module:
                # Auto-detect from first file
                modules = self.extract_modules(str(verilog_files[0]))
                if modules:
                    top_module = modules[0]
                    print(f"✅ Auto-detected: {top_module}\n")
                    break
                else:
                    print("❌ Could not auto-detect top module")
                    print("   Please enter module name manually\n")
                    continue
            else:
                print(f"✅ Using: {top_module}\n")
                break
        
        # Get output directory
        while True:
            print("📂 Enter output directory path:")
            print("   Example: D:\\langG\\output")
            print("   (Press Enter for current directory)")
            output_dir = input("Output directory: ").strip() or "."
            
            try:
                output_path = Path(output_dir).resolve()
                output_path.mkdir(parents=True, exist_ok=True)
                print(f"✅ Output directory: {output_path}\n")
                break
            except Exception as e:
                print(f"❌ Error: {e}\n")
                continue
        
        return input_path, output_dir, top_module
    
    def synthesize(self, input_file: str, output_dir: str, 
                   top_module: str) -> SynthesisResult:
        """
        Main synthesis method.
        """
        start_time = time.time()
        messages = []
        
        try:
            # Find input files
            print("\n" + "="*70)
            print("SYNTHESIS PROCESS")
            print("="*70 + "\n")
            
            print("📁 Finding Verilog files...")
            verilog_files = self.find_verilog_files(input_file)
            print(f"   ✅ Found {len(verilog_files)} file(s)\n")
            
            # Prepare output
            print("📂 Preparing output...")
            output_path = Path(output_dir).resolve()
            output_path.mkdir(parents=True, exist_ok=True)
            output_file = output_path / f"{top_module}_synthesized.v"
            
            # Check if file exists and backup
            if output_file.exists():
                backup = output_file.with_suffix(f".backup_{int(time.time())}.v")
                output_file.rename(backup)
                print(f"   ⚠️  Backed up existing file to: {backup.name}")
            
            print(f"   ✅ Output file: {output_file.name}\n")
            
            # Build Yosys script
            print("🛠️  Building Yosys script...")
            script_lines = [
                f"# Synthesis script for {top_module}",
                f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                ""
            ]
            
            # Add read commands
            for vf in verilog_files:
                norm_path = self.normalize_path(vf)
                script_lines.append(f"read_verilog {norm_path}")
                print(f"   ✅ Added: {vf.name}")
            
            print()
            
            script_lines.append("")
            
            # Add synthesis commands
            script_lines.extend([
                f"hierarchy -check -top {top_module}",
                "proc",
                "opt",
                "memory",
                "opt",
                "fsm",
                "opt",
                "techmap",
                "opt",
                "abc -g AND,NAND,OR,NOR,XOR,XNOR,MUX",
                "clean",
                "stat",
                f"write_verilog {self.normalize_path(output_file)}"
            ])
            
            yosys_script = "\n".join(script_lines)
            
            # Write and run Yosys
            print("🚀 Running Yosys synthesis...\n")
            
            script_file = self.work_dir / f"synth_{int(time.time())}.ys"
            script_file.write_text(yosys_script, encoding='utf-8')
            
            # Execute Yosys
            cmd = [self.yosys_path, "-s", str(script_file)]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(self.work_dir)
            )
            
            # Display Yosys output
            print("-" * 70)
            print("YOSYS OUTPUT")
            print("-" * 70)
            
            if result.stdout:
                print(result.stdout)
            
            if result.stderr:
                print(result.stderr)
            
            print("-" * 70 + "\n")
            
            # Cleanup script
            script_file.unlink(missing_ok=True)
            
            # Check result
            if result.returncode == 0 and output_file.exists():
                try:
                    netlist_content = output_file.read_text(encoding='utf-8')
                except:
                    netlist_content = ""
                
                execution_time = time.time() - start_time
                
                print("="*70)
                print("✅ SYNTHESIS SUCCESSFUL")
                print("="*70)
                print(f"📄 Output file: {output_file}")
                print(f"📊 File size: {output_file.stat().st_size} bytes")
                print(f"⏱️  Time: {execution_time:.2f} seconds")
                print("="*70 + "\n")
                
                return SynthesisResult(
                    success=True,
                    stage="completed",
                    output_file=str(output_file),
                    netlist_content=netlist_content,
                    execution_time=execution_time,
                    messages=messages
                )
            else:
                execution_time = time.time() - start_time
                
                print("="*70)
                print("❌ SYNTHESIS FAILED")
                print("="*70)
                print(f"Return code: {result.returncode}")
                if not output_file.exists():
                    print("Output file was not created")
                print(f"⏱️  Time: {execution_time:.2f} seconds")
                print("="*70 + "\n")
                
                return SynthesisResult(
                    success=False,
                    stage="failed",
                    execution_time=execution_time,
                    messages=messages
                )
        
        except Exception as e:
            execution_time = time.time() - start_time
            
            print("="*70)
            print("❌ ERROR")
            print("="*70)
            print(f"Error: {str(e)}")
            print(f"⏱️  Time: {execution_time:.2f} seconds")
            print("="*70 + "\n")
            
            return SynthesisResult(
                success=False,
                stage="error",
                execution_time=execution_time,
                messages=messages
            )
    
    
    def cleanup(self):
        """Clean up temporary files."""
        try:
            for f in self.work_dir.glob("synth_*.ys"):
                f.unlink(missing_ok=True)
            print("✅ Cleanup completed\n")
        except:
            pass


# ============================================================================
# MAIN INTERACTIVE APPLICATION
# ============================================================================

def main():
    """Main entry point with interactive interface."""
    try:
        # Create wrapper
        wrapper = YosysWrapper()
        
        # Get user input
        input_path, output_dir, top_module = wrapper.get_user_input()
        
        # Confirm parameters
        print("="*70)
        print("CONFIRM PARAMETERS")
        print("="*70)
        print(f"📁 Input: {Path(input_path).resolve()}")
        print(f"📂 Output: {Path(output_dir).resolve()}")
        print(f"🎯 Top module: {top_module}")
        print("="*70)
        
        confirm = input("\nProceed with synthesis? (yes/no): ").strip().lower()
        
        if confirm not in ['yes', 'y']:
            print("\n❌ Synthesis cancelled by user\n")
            return
        
        print()
        
        # Run synthesis
        result = wrapper.synthesize(
            input_file=input_path,
            output_dir=output_dir,
            top_module=top_module
        )
        
        # Show result summary
        if result.success:
            print("\n🎉 Synthesis completed successfully!")
            print(f"📄 Output saved to: {result.output_file}")
        else:
            print("\n⚠️  Synthesis did not complete successfully")
            print("Check the Yosys output above for error details")
        
        # Cleanup
        wrapper.cleanup()
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Synthesis interrupted by user\n")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}\n")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
