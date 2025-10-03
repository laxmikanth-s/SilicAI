import subprocess
import os
import platform
from pathlib import Path

class OpenROADWrapperError(Exception):
    """Custom exception for OpenROAD wrapper errors."""
    pass

class OpenROADGUIWrapper:
    def __init__(self):
        self.openroad_path = None
        self.use_wsl = False
        self._find_openroad_executable()
        
        if self.openroad_path:
            wsl_msg = " (via WSL)" if self.use_wsl else ""
            print(f"OpenROAD found at: {self.openroad_path}{wsl_msg}")
        else:
            print("OpenROAD executable not found.")

    def _check_wsl_available(self):
        """Check if WSL is available on Windows."""
        try:
            result = subprocess.run(['wsl', '--status'], 
                                  capture_output=True, text=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _convert_windows_path_to_wsl(self, windows_path):
        """Convert Windows path to WSL path format."""
        if windows_path.startswith('D:'):
            wsl_path = windows_path.replace('D:', '/mnt/d').replace('\\', '/')
            return wsl_path
        elif windows_path.startswith('C:'):
            wsl_path = windows_path.replace('C:', '/mnt/c').replace('\\', '/')
            return wsl_path
        else:
            drive = windows_path[0].lower()
            wsl_path = windows_path.replace(f'{drive.upper()}:', f'/mnt/{drive}').replace('\\', '/')
            return wsl_path

    def _test_linux_executable_with_wsl(self, filepath):
        """Test if Linux executable works through WSL."""
        if not self._check_wsl_available():
            return False
        
        wsl_path = self._convert_windows_path_to_wsl(filepath)
        try:
            result = subprocess.run(['wsl', wsl_path, '--version'], 
                                  capture_output=True, text=True, 
                                  check=False, timeout=10)
            return result.returncode == 0 or "openroad" in result.stdout.lower()
        except Exception:
            return False

    def _find_openroad_executable(self):
        """Find OpenROAD executable - Linux binary that works through WSL."""
        
        openroad_base_dir = r"D:\OpenROAD"
        
        linux_executable_paths = [
            os.path.join(openroad_base_dir, "build", "src", "openroad"),
            os.path.join(openroad_base_dir, "build/src/openroad"),
            os.path.join(openroad_base_dir, "bin", "openroad"),
            os.path.join(openroad_base_dir, "openroad"),
        ]
        
        print("Searching for OpenROAD Linux executable...")
        
        if not self._check_wsl_available():
            print("WSL not available. Install WSL to run Linux OpenROAD executable.")
            return
        
        for path in linux_executable_paths:
            if os.path.exists(path):
                if self._test_linux_executable_with_wsl(path):
                    print(f"    ✓ Linux executable works with WSL!")
                    self.openroad_path = path
                    self.use_wsl = True
                    return

    def _run_command_with_wsl(self, cmd_args, working_dir=None):
        """Run command through WSL with proper working directory."""
        wsl_openroad_path = self._convert_windows_path_to_wsl(self.openroad_path)
        
        wsl_cmd_args = []
        for arg in cmd_args[1:]:
            if os.path.exists(arg) and '\\' in arg:
                wsl_cmd_args.append(self._convert_windows_path_to_wsl(arg))
            else:
                wsl_cmd_args.append(arg)
        
        if working_dir:
            wsl_working_dir = self._convert_windows_path_to_wsl(working_dir)
            full_cmd = ['wsl', 'bash', '-c', f'cd "{wsl_working_dir}" && "{wsl_openroad_path}" {" ".join(wsl_cmd_args)}']
        else:
            full_cmd = ['wsl', wsl_openroad_path] + wsl_cmd_args
        
        return full_cmd

    def run_script_gui(self, script_path):
        """Launch OpenROAD in GUI mode with the given TCL script."""
        if not self.openroad_path:
            raise OpenROADWrapperError("OpenROAD executable not found.")
        
        if not os.path.exists(script_path):
            raise OpenROADWrapperError(f"TCL script file not found: {script_path}")

        script_dir = os.path.dirname(os.path.abspath(script_path))
        script_name = os.path.basename(script_path)
        
        if self.use_wsl:
            cmd = self._run_command_with_wsl([self.openroad_path, "-gui", script_name], working_dir=script_dir)
        else:
            cmd = [self.openroad_path, "-gui", script_path]
        
        try:
            print(f"Launching OpenROAD GUI from directory: {script_dir}")
            print(f"Running script: {script_name}")
            
            if self.use_wsl:
                subprocess.run(cmd, check=True)
            else:
                subprocess.run(cmd, check=True, cwd=script_dir)
                
            print("OpenROAD GUI session completed.")
        except subprocess.CalledProcessError as e:
            raise OpenROADWrapperError(f"OpenROAD GUI failed with exit code {e.returncode}") from e
        except Exception as e:
            raise OpenROADWrapperError(f"Unexpected error: {str(e)}") from e

    def find_tcl_scripts(self, search_dir=r"D:\OpenROAD"):
        """Find TCL scripts in the OpenROAD directory."""
        tcl_scripts = []
        try:
            for root, dirs, files in os.walk(search_dir):
                for file in files:
                    if file.endswith('.tcl'):
                        tcl_scripts.append(os.path.join(root, file))
        except Exception as e:
            print(f"Error searching for TCL scripts: {e}")
        
        return tcl_scripts

    def get_script_path_interactive(self):
        """Interactive method to get script path with multiple options."""
        print("\n=== TCL Script Selection ===")
        
        # Option 1: Use default gcd_nangate45.tcl
        default_script = r"D:\OpenROAD\test\gcd_nangate45.tcl"
        if os.path.exists(default_script):
            print(f"1. Use default script: {default_script}")
            choice = input("Press 1 to use default, or Enter to see other options: ").strip()
            if choice == "1":
                return default_script
        
        # Option 2: Show available TCL scripts
        print("\n2. Available TCL scripts found:")
        tcl_scripts = self.find_tcl_scripts()
        
        if tcl_scripts:
            for i, script in enumerate(tcl_scripts[:10], 1):  # Show first 10
                print(f"   {i}. {script}")
            
            try:
                choice = input(f"\nEnter number (1-{min(len(tcl_scripts), 10)}) or press Enter to type path manually: ").strip()
                if choice.isdigit() and 1 <= int(choice) <= min(len(tcl_scripts), 10):
                    return tcl_scripts[int(choice) - 1]
            except (ValueError, IndexError):
                pass
        
        # Option 3: Manual path entry with better prompt
        print("\n3. Manual path entry:")
        print("Examples:")
        print("   D:\\OpenROAD\\test\\gcd_nangate45.tcl")
        print("   D:\\OpenROAD\\examples\\example.tcl")
        
        while True:
            try:
                script_path = input("\nEnter full path to TCL script (or 'exit' to quit): ").strip()
                
                if script_path.lower() == 'exit':
                    exit()
                
                if not script_path:
                    print("Please enter a valid path.")
                    continue
                
                # Remove quotes if user added them
                script_path = script_path.strip('"').strip("'")
                
                if os.path.exists(script_path):
                    return script_path
                else:
                    print(f"File not found: {script_path}")
                    print("Please check the path and try again.")
                    
            except KeyboardInterrupt:
                print("\nExiting...")
                exit()
            except Exception as e:
                print(f"Error: {e}")

def main():
    # Create wrapper instance
    openroad = OpenROADGUIWrapper()
    
    print("=== OpenROAD Linux Executable on Windows ===")
    
    if not openroad.openroad_path:
        print("ERROR: OpenROAD executable not found.")
        print("Please ensure OpenROAD is installed and WSL is available.")
        return
    
    print("OpenROAD found successfully!")
    
    try:
        # Get script path using interactive method
        script_file = openroad.get_script_path_interactive()
        
        # Choose mode
        print(f"\nSelected script: {script_file}")
        mode = input("Choose mode - (g)ui or (t)erminal [default: gui]: ").strip().lower()
        
        # Run the script
        if mode.startswith('t'):
            output = openroad.run_script_terminal(script_file)
            print("Script Output:")
            print(output)
        else:
            openroad.run_script_gui(script_file)
            
    except OpenROADWrapperError as e:
        print("ERROR:", e)
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Unexpected error: {e}")

if __name__ == "__main__":
    main()
