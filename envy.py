#!/usr/bin/env python3

import os
import sys
import subprocess
import traceback
import argparse
import shutil
from pathlib import Path
import threading
import logging
import tempfile
import time

# Define constants
APP_NAME = "envy"
DEFAULT_VENV_PATH = Path.home() / ".local" / APP_NAME

# Module to Package Mapping (all keys in lowercase for case-insensitive matching)
MODULE_PACKAGE_MAP = {
    'pil': 'Pillow',
    'qtawesome': 'qtawesome',
    'qt_material': 'qt-material',
    # TODO Add other mappings if needed
}

# Secondary modules to install last
SECONDARY_MODULES = {'qtawesome', 'qt_material'}

def resource_path(relative_path):
    """
    Get absolute path to resource, works for dev and for PyInstaller.
    """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = Path(sys._MEIPASS)
    except Exception:
        base_path = Path(__file__).parent
    return base_path / relative_path

def get_log_file():
    """
    Get the path to the log file in the user's cache directory.
    """
    xdg_cache_home = os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')
    log_dir = Path(xdg_cache_home) / APP_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "envy_launcher.log"

LOG_FILE = get_log_file()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

def check_pipreqs_installed():
    """
    Check if pipx and pipreqs are installed via pipx.
    If pipreqs is not installed, attempt to install it.
    """
    try:
        # Check if pipx is installed
        subprocess.check_call(['pipx', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        logging.error("pipx is not installed. Please install it using 'python3 -m pip install --user pipx' and try again.")
        sys.exit(1)
    except FileNotFoundError:
        logging.error("pipx command not found. Ensure pipx is installed and added to your PATH.")
        sys.exit(1)
    
    try:
        # List pipx packages and check for pipreqs
        result = subprocess.check_output(['pipx', 'list'], text=True)
        if 'pipreqs' in result:
            logging.info("pipreqs is already installed via pipx.")
        else:
            logging.info("pipreqs is not installed via pipx. Attempting to install pipreqs now...")
           
            # Attempt to install pipreqs via pipx
            subprocess.check_call(['pipx', 'install', 'pipreqs'])
            logging.info("pipreqs has been successfully installed via pipx.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to list pipx packages or install pipreqs: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Unexpected error while checking or installing pipreqs: {e}")
        traceback.print_exc()
        sys.exit(1)

def open_log_file():
    """
    Open the log file using the default system application.
    Supports Linux (xdg-open), macOS (open), and Windows (start).
    """
    try:
        if sys.platform.startswith('linux'):
            subprocess.check_call(['xdg-open', str(LOG_FILE)])
        elif sys.platform == 'darwin':
            subprocess.check_call(['open', str(LOG_FILE)])
        elif sys.platform == 'win32':
            os.startfile(str(LOG_FILE))
        else:
            logging.error("Unsupported OS for opening log file.")
            return
        logging.info(f"Opened log file {LOG_FILE}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to open log file {LOG_FILE}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error while opening log file {LOG_FILE}: {e}")

def ensure_venv(venv_path):
    """
    Ensure the virtual environment exists and create it if necessary.
    Uses the system's Python interpreter when running as a frozen app to avoid recursion.
    """
    if not venv_path.exists():
        logging.info(f"Creating virtual environment at {venv_path}")
        try:
            if getattr(sys, 'frozen', False):
                # Running as a frozen app; use 'python3' to create the venv
                python_executable = shutil.which('python3')
                if not python_executable:
                    logging.error("python3 is not found in PATH. Please install Python 3 and ensure it's accessible.")
                    sys.exit(1)
                subprocess.check_call([python_executable, "-m", "venv", str(venv_path)])
            else:
                # Running as a normal script; use sys.executable
                subprocess.check_call([sys.executable, "-m", "venv", str(venv_path)])
            logging.info("Virtual environment created successfully.")
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to create virtual environment: {e}")
            sys.exit(1)
        except Exception as e:
            logging.error(f"Unexpected error while creating virtual environment: {e}")
            traceback.print_exc()
            sys.exit(1)
    else:
        logging.info(f"Using existing virtual environment at {venv_path}")

def install_dependencies(venv_path, requirements=None):
    """
    Install dependencies from a requirements file.
    """
    pip_executable = venv_path / ("Scripts" if os.name == "nt" else "bin") / "pip"
    if requirements:
        if not requirements.exists():
            logging.error(f"Requirements file {requirements} does not exist.")
            sys.exit(1)
        logging.info(f"Installing dependencies from {requirements}")
        try:
            subprocess.check_call([str(pip_executable), "install", "-r", str(requirements)])
            logging.info("Dependencies installed successfully.")
        except subprocess.CalledProcessError as e:
            logging.error(f"Error installing dependencies: {e}")
            sys.exit(1)
    else:
        logging.info("No requirements file provided.")

def run_app_in_venv(venv_path, app_path, app_args):
    """
    Run the app inside the virtual environment.
    """
    python_executable = venv_path / ("Scripts" if os.name == "nt" else "bin") / "python"
    try:
        logging.info(f"Running {app_path} in virtual environment with arguments: {app_args}")
        # Include the app directory in PYTHONPATH to handle local imports
        app_dir = app_path.parent
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{str(app_dir)}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(app_dir)
        subprocess.check_call([str(python_executable), str(app_path)] + app_args, env=env)
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Error while running the app: {e}")
        return False
    except Exception as e:
        logging.error("Unexpected error while running the app:")
        traceback.print_exc()
        return False

def find_missing_modules(error_message):
    """
    Extract missing module names from ImportError messages or custom errors.
    """
    import re
    # Extended regex to capture more patterns
    missing_modules = re.findall(r"No module named ['\"]([\w\d_]+)['\"]", error_message)
    missing_modules += re.findall(r"ImportError: cannot import name ['\"]([\w\d_]+)['\"]", error_message)
    
    # Detect custom QtBindingsNotFoundError
    if "qtpy.QtBindingsNotFoundError: No Qt bindings could be found" in error_message:
        # Suggest installing PyQt5 as the default Qt binding
        missing_modules.append('PyQt5')
    
    return missing_modules

def install_single_dependency(venv_path, package):
    """
    Install a single missing package into the virtual environment.
    """
    pip_executable = venv_path / ("Scripts" if os.name == "nt" else "bin") / "pip"
    logging.info(f"Installing missing package: {package}")
    try:
        subprocess.check_call([str(pip_executable), "install", package])
        logging.info(f"Successfully installed {package}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error installing {package}: {e}")
        logging.error(f"Failed to install {package}.")
        sys.exit(1)

def uninstall_dependency(venv_path, package):
    """
    Uninstall a single package from the virtual environment.
    """
    pip_executable = venv_path / ("Scripts" if os.name == "nt" else "bin") / "pip"
    logging.info(f"Uninstalling package: {package}")
    try:
        subprocess.check_call([str(pip_executable), "uninstall", "-y", package])
        logging.info(f"Successfully uninstalled {package}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error uninstalling {package}: {e}")
        logging.error(f"Failed to uninstall {package}.")
        sys.exit(1)

def update_all_packages(venv_path):
    """
    Update all packages in the virtual environment to their latest versions.
    """
    pip_executable = venv_path / ("Scripts" if os.name == "nt" else "bin") / "pip"
    try:
        logging.info("Updating pip to the latest version...")
        subprocess.check_call([str(pip_executable), "install", "--upgrade", "pip"])
        
        logging.info("Retrieving list of installed packages...")
        installed = subprocess.check_output([str(pip_executable), 'freeze'], text=True)
        packages = [line.strip().split('==')[0] for line in installed.splitlines() if '==' in line]
        
        if packages:
            logging.info(f"Updating {len(packages)} packages...")
            subprocess.check_call([str(pip_executable), "install", "--upgrade"] + packages)
            logging.info("All packages have been updated successfully.")
        else:
            logging.info("No packages found to update.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Error while updating packages: {e}")
    except Exception as e:
        logging.error("Unexpected error during package update:")
        traceback.print_exc()

def generate_requirements(app_path):
    """
    Generate a requirements.txt file using pipreqs via pipx based solely on the main app and its local imports.
    """
    try:
        logging.info(f"Generating requirements.txt using pipreqs for {app_path}")
        
        # Create a temporary directory
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            # Copy the main app and its local imports to the temporary directory
            copy_local_files(app_path, temp_path)
            
            # Run pipreqs via pipx on the temporary directory
            subprocess.check_call(['pipx', 'run', 'pipreqs', str(temp_path), '--force', '--savepath', str(temp_path / 'requirements.txt')])
            requirements_path = temp_path / 'requirements.txt'
            logging.info(f"Requirements file generated at {requirements_path}")
            
            # Read the requirements.txt content
            with requirements_path.open('r') as f:
                requirements_content = f.read()
            
            # Write the requirements to a permanent location (app directory)
            permanent_requirements = app_path.parent / 'requirements.txt'
            with permanent_requirements.open('w') as f:
                f.write(requirements_content)
            logging.info(f"Copied requirements.txt to {permanent_requirements}")
            
            return permanent_requirements
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to generate requirements.txt using pipreqs via pipx: {e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Error during requirements generation: {e}")
        traceback.print_exc()
        sys.exit(1)

def copy_local_files(app_path, temp_path):
    """
    Recursively copy the main app and its local imports to the temporary directory.
    """
    try:
        app_dir = app_path.parent
        # Read the main app's imports
        with app_path.open('r') as f:
            import_lines = f.readlines()
        
        import_modules = []
        for line in import_lines:
            line = line.strip()
            if line.startswith('import ') or line.startswith('from '):
                # Simple parsing, may not cover all cases
                parts = line.split()
                if parts[0] == 'import':
                    module = parts[1].split('.')[0]
                    import_modules.append(module)
                elif parts[0] == 'from':
                    module = parts[1].split('.')[0]
                    import_modules.append(module)
        
        # Assume local modules are in the same directory as the main app
        for module in import_modules:
            # Skip standard library and common modules
            if module.lower() in ['sys', 'os', 'logging', 'subprocess', 'argparse', 'shutil', 'pathlib', 'threading', 'tempfile', 're', 'traceback']:
                continue
            potential_file = app_dir / f"{module}.py"
            potential_dir = app_dir / module
            if potential_file.exists():
                shutil.copy2(potential_file, temp_path)
                logging.info(f"Copied local module file: {potential_file}")
            elif potential_dir.exists() and (potential_dir / "__init__.py").exists():
                shutil.copytree(potential_dir, temp_path / module)
                logging.info(f"Copied local module package: {potential_dir}")
        
        # Finally, copy the main app
        shutil.copy2(app_path, temp_path)
        logging.info(f"Copied main application file: {app_path}")
        
    except Exception as e:
        logging.error(f"Error copying local files: {e}")
        traceback.print_exc()
        sys.exit(1)

def recreate_venv(venv_path, app_path=None):
    """
    Recreate the virtual environment by deleting and creating it anew.
    """
    try:
        if venv_path.exists():
            logging.info(f"Removing existing virtual environment at {venv_path}")
            shutil.rmtree(venv_path)
        else:
            logging.info(f"No existing virtual environment found at {venv_path}. Proceeding to create one.")
        
        # Create a new virtual environment
        ensure_venv(venv_path)
        
        # If app_path is provided, generate and install dependencies
        if app_path:
            logging.info(f"Detecting dependencies from {app_path}")
            requirements = generate_requirements(app_path)
            if requirements.exists():
                logging.info(f"Installing dependencies from {requirements}")
                install_dependencies(venv_path, requirements)
                # Clean up the generated requirements file
                try:
                    requirements.unlink()
                    logging.info(f"Removed temporary requirements file {requirements}")
                except Exception as e:
                    logging.warning(f"Could not remove temporary requirements file {requirements}: {e}")
            else:
                logging.warning("No dependencies detected from imports.")
        else:
            logging.info("No application path provided. Virtual environment recreated without installing dependencies.")
        
        logging.info("Virtual environment has been recreated successfully.")
    except Exception as e:
        logging.error(f"Error while recreating the virtual environment: {e}")
        traceback.print_exc()
        sys.exit(1)

def delete_venv(venv_path):
    """
    Delete the specified virtual environment.
    """
    try:
        if venv_path.exists():
            logging.info(f"Deleting virtual environment at {venv_path}")
            shutil.rmtree(venv_path)
            logging.info("Virtual environment deleted successfully.")
        else:
            logging.warning(f"Virtual environment at {venv_path} does not exist.")
    except Exception as e:
        logging.error(f"Error while deleting the virtual environment: {e}")
        traceback.print_exc()
        sys.exit(1)

def handle_missing_dependencies(venv_path, app_path, app_args, force_install=False):
    """
    Handle missing dependencies by identifying and installing them.
    If force_install is True, install dependencies without confirmation.
    """
    python_executable = venv_path / ("Scripts" if os.name == "nt" else "bin") / "python"
    try:
        # Run the app and capture stderr
        result = subprocess.run(
            [str(python_executable), str(app_path)] + app_args,
            stderr=subprocess.PIPE,
            text=True
        )
        if result.returncode != 0:
            error_message = result.stderr
            logging.error(f"Captured error message: {error_message}")
            missing_modules = find_missing_modules(error_message)
            logging.info(f"Missing modules detected: {missing_modules}")
            if missing_modules:
                # Separate primary and secondary modules
                primary_modules = []
                secondary_modules_detected = []
                for module in missing_modules:
                    if module.lower() in SECONDARY_MODULES:
                        secondary_modules_detected.append(module)
                    else:
                        primary_modules.append(module)
                
                # Install primary modules first
                for module in primary_modules:
                    # Skip local modules
                    if is_local_module(venv_path, app_path, module):
                        logging.info(f"Detected missing local module: {module}, skipping pip installation.")
                        continue
                    # Map module to package if mapping exists (case-insensitive)
                    package = MODULE_PACKAGE_MAP.get(module.lower(), module)
                    logging.info(f"Detected missing module: {module}, mapped to package: {package}")
                    install_single_dependency(venv_path, package)
                
                # Install secondary modules last
                for module in secondary_modules_detected:
                    # Skip local modules
                    if is_local_module(venv_path, app_path, module):
                        logging.info(f"Detected missing local module: {module}, skipping pip installation.")
                        continue
                    # Map module to package if mapping exists (case-insensitive)
                    package = MODULE_PACKAGE_MAP.get(module.lower(), module)
                    logging.info(f"Detected missing module: {module}, mapped to package: {package}")
                    install_single_dependency(venv_path, package)
                
                return True
            else:
                # Check for specific custom errors
                if "qtpy.QtBindingsNotFoundError: No Qt bindings could be found" in error_message:
                    logging.info("Detected QtBindingsNotFoundError. Installing PyQt5 as the default Qt binding.")
                    install_single_dependency(venv_path, "PyQt5")
                    return True
                logging.error("Could not determine the missing dependency.")
    except subprocess.SubprocessError as e:
        logging.error(f"Subprocess error: {e}")
    except Exception as e:
        logging.error("Unexpected error while handling dependencies:")
        traceback.print_exc()
    return False

def is_local_module(venv_path, app_path, module):
    """
    Determine if the missing module is a local module.
    """
    app_dir = app_path.parent
    # Check for module.py
    local_module = app_dir / f"{module}.py"
    # Check for module package (module/__init__.py)
    local_package_init = app_dir / module / "__init__.py"
    if local_module.exists() or local_package_init.exists():
        return True
    return False

def parse_arguments():
    """
    Parse command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Envy - Python App Launcher with Virtual Environment Management.\n\n"
            "Usage examples:\n"
            f"  1. Launch app without additional arguments:\n"
            f"     {APP_NAME}.py -v /path/to/envy -a /path/to/app.py\n\n"
            "  2. Launch app with additional arguments:\n"
            f"     {APP_NAME}.py -v /path/to/envy -a /path/to/app.py -- -m --verbose\n\n"
            "  3. Update all packages in the virtual environment:\n"
            f"     {APP_NAME}.py -v /path/to/envy --update\n\n"
            "  4. Recreate the virtual environment (dependencies auto-detected):\n"
            f"     {APP_NAME}.py -v /path/to/envy --recreate -a /path/to/app.py\n\n"
            "  5. Recreate the virtual environment without launching an app:\n"
            f"     {APP_NAME}.py -v /path/to/envy --recreate\n\n"
            "  6. Delete the virtual environment:\n"
            f"     {APP_NAME}.py -v /path/to/envy --delete\n\n"
            "  7. Install a specific module into the virtual environment:\n"
            f"     {APP_NAME}.py -v /path/to/envy -p package_name\n\n"
            "  8. Uninstall a specific module from the virtual environment:\n"
            f"     {APP_NAME}.py -v /path/to/envy --unpip package_name\n\n"
            "  9. Open the log file:\n"
            f"     {APP_NAME}.py -l\n\n"
            " 10. Create the virtual environment and install dependencies without launching an app:\n"
            f"     {APP_NAME}.py -v /path/to/envy -r /path/to/requirements.txt\n\n"
            " 11. Force install missing dependencies without prompting:\n"
            f"     {APP_NAME}.py -v /path/to/envy -a /path/to/app.py --force-install\n\n"
            " 12. Set fixed delay to 5 seconds between attempts:\n"
            f"     {APP_NAME}.py -v /path/to/envy -a /path/to/app.py --fixed-delay 5\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "-v", "--venv",
        type=str,
        default=str(DEFAULT_VENV_PATH),
        help=f"Path to the virtual environment folder (default: {DEFAULT_VENV_PATH})"
    )
    parser.add_argument(
        "-a", "--app",
        type=str,
        help="Path to the Python application to launch"
    )
    parser.add_argument(
        "-r", "--requirements",
        type=str,
        help="Path to the requirements.txt file"
    )
    # New arguments
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-u", "--update",
        action="store_true",
        help="Update all packages in the virtual environment"
    )
    group.add_argument(
        "--recreate",
        action="store_true",
        help="Recreate the virtual environment by deleting and creating it anew (auto-detect dependencies if -a is provided)"
    )
    group.add_argument(
        "-d", "--delete",
        action="store_true",
        help="Delete the specified virtual environment"
    )
    group.add_argument(
        "-p", "--pip",
        type=str,
        nargs='+',
        help="Install one or more packages into the virtual environment (e.g., -p package1 package2)"
    )
    group.add_argument(
        "-u2", "--unpip",
        type=str,
        nargs='+',
        help="Uninstall one or more packages from the virtual environment (e.g., --unpip package1 package2)"
    )
    group.add_argument(
        "-l", "--log",
        action='store_true',
        help="Open the log file using the default system application (xdg-open)"
    )
    # New flags for force installing dependencies without prompting
    parser.add_argument(
        "--force-install",
        action="store_true",
        help="Automatically install missing dependencies without prompting for confirmation."
    )
    # New flags for delay configuration
    parser.add_argument(
        "--fixed-delay",
        type=int,
        default=2,
        help="Fixed delay in seconds between retry attempts (default: 2 seconds)."
    )
    # Remove --max-delay argument
    # parser.add_argument(
    #     "--max-delay",
    #     type=int,
    #     default=60,
    #     help="Maximum delay in seconds between retry attempts (default: 60 seconds)."
    # )
    # Use parse_known_args to capture unknown arguments
    known_args, unknown_args = parser.parse_known_args()
    return known_args, unknown_args

def main():
    known_args, app_args = parse_arguments()

    # Handle --log argument
    if known_args.log:
        open_log_file()
        sys.exit(0)

    # Check if pipreqs is installed via pipx, install if not
    check_pipreqs_installed()

    # Debugging: Print parsed arguments
    logging.info(f"Known args: venv={known_args.venv}, app={known_args.app}, requirements={known_args.requirements}, "
                 f"update={known_args.update}, recreate={known_args.recreate}, delete={known_args.delete}, "
                 f"pip={known_args.pip}, unpip={known_args.unpip}, force_install={known_args.force_install}, "
                 f"fixed_delay={known_args.fixed_delay}")
    logging.info(f"App args: {app_args}")

    venv_path = Path(known_args.venv).expanduser().resolve()
    app_path = Path(known_args.app).resolve() if known_args.app else None
    requirements = Path(known_args.requirements).resolve() if known_args.requirements else None
    pip_packages = known_args.pip if known_args.pip else []
    uninstall_packages = known_args.unpip if known_args.unpip else []
    force_install = known_args.force_install
    delay_seconds = known_args.fixed_delay  # Using fixed_delay instead of initial_delay

    if app_path and not app_path.exists() and not (known_args.delete or known_args.unpip):
        logging.error(f"Application path {app_path} does not exist.")
        sys.exit(1)

    # Thread lock to ensure thread safety during venv operations
    venv_lock = threading.Lock()

    with venv_lock:
        # Handle --delete argument
        if known_args.delete:
            delete_venv(venv_path)
            sys.exit(0)

        # Handle --recreate argument
        if known_args.recreate:
            if app_path:
                # Auto-detect dependencies using pipreqs via pipx
                recreate_venv(venv_path, app_path=app_path)
            else:
                # Recreate without installing dependencies (empty env)
                recreate_venv(venv_path, app_path=None)

        # Handle --update argument
        elif known_args.update:
            ensure_venv(venv_path)
            update_all_packages(venv_path)

        # Handle --pip argument
        elif pip_packages:
            ensure_venv(venv_path)
            for package in pip_packages:
                install_single_dependency(venv_path, package)

        # Handle --unpip argument
        elif uninstall_packages:
            ensure_venv(venv_path)
            for package in uninstall_packages:
                uninstall_dependency(venv_path, package)

        else:
            # If not recreating, updating, installing, or uninstalling, ensure venv exists
            ensure_venv(venv_path)

            # Install dependencies if a requirements file is provided
            if requirements:
                install_dependencies(venv_path, requirements)

    # If -a/--app is provided, attempt to run the application
    if app_path and not (known_args.delete or known_args.unpip):
        # Ensure the virtual environment exists
        ensure_venv(venv_path)
        
        # Install dependencies if a requirements file is provided
        if requirements:
            install_dependencies(venv_path, requirements)
        
        # Attempt to run the application in a separate thread to ensure resource efficiency
        app_thread = threading.Thread(
            target=run_application, 
            kwargs={
                'venv_path': venv_path, 
                'app_path': app_path, 
                'app_args': app_args, 
                'force_install': force_install, 
                'delay_seconds': delay_seconds
            }
        )
        app_thread.start()
        app_thread.join()

def run_application(venv_path, app_path, app_args, force_install=False, delay_seconds=2):
    """
    Run the application and handle missing dependencies.
    Attempts to run the app up to max_attempts times with fixed delays between attempts.
    
    Parameters:
        venv_path (Path): Path to the virtual environment.
        app_path (Path): Path to the Python application to run.
        app_args (list): List of arguments to pass to the application.
        force_install (bool): Whether to force install dependencies without prompting.
        delay_seconds (int): Number of seconds to wait between attempts.
    """
    max_attempts = 12  # Increased to handle multiple missing dependencies
    attempt = 1
    while attempt <= max_attempts:
        logging.info(f"Attempt {attempt} to run the application.")
        success = run_app_in_venv(venv_path, app_path, app_args)
        if success:
            logging.info("Application ran successfully.")
            return
        else:
            if attempt < max_attempts:
                logging.info("Application failed to run. Attempting to resolve missing dependencies.")
                dependencies_resolved = handle_missing_dependencies(
                    venv_path, app_path, app_args, force_install=force_install
                )
                if dependencies_resolved:
                    logging.info("Dependencies resolved. Retrying to run the application.")
                else:
                    logging.error("Failed to resolve dependencies. Exiting.")
                    sys.exit(1)
                logging.info(f"Waiting for {delay_seconds} seconds before next attempt.")
                time.sleep(delay_seconds)  # Fixed delay
            attempt += 1
    # If reached here, it means all attempts failed
    logging.error("All attempts to run the application have failed.")
    sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Process interrupted by user. Exiting gracefully.")
        sys.exit(0)
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        traceback.print_exc()
        sys.exit(1)
