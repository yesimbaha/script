import os
import subprocess
import sys
import time
import platform
from pathlib import Path

def start_backend():
    """Start the backend server using uvicorn"""
    print("Starting backend server...")
    backend_dir = Path("backend").absolute()
    
    # Check if .env file exists, create it if not
    env_file = backend_dir / ".env"
    if not env_file.exists():
        print("Creating .env file with default configuration...")
        with open(env_file, "w") as f:
            f.write("MONGO_URL=mongodb://localhost:27017\n")
            f.write("DB_NAME=tankpit_bot\n")
    
    # Start the backend server
    backend_cmd = [sys.executable, "-m", "uvicorn", "server:app", "--reload"]
    backend_process = subprocess.Popen(
        backend_cmd,
        cwd=str(backend_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )
    
    # Wait for backend to start
    print("Waiting for backend to start...")
    for _ in range(30):  # Wait up to 30 seconds
        line = backend_process.stdout.readline()
        print(f"Backend: {line.strip()}")
        if "Application startup complete" in line:
            print("Backend started successfully!")
            break
        time.sleep(1)
    
    return backend_process

def start_frontend():
    """Start the frontend using npm"""
    print("Starting frontend application...")
    frontend_dir = Path("frontend").absolute()
    
    # Start the frontend using shell=True on Windows to find npm in PATH
    if os.name == 'nt':  # Windows
        frontend_cmd = "npm run start"
        shell = True
    else:  # Linux/Mac
        frontend_cmd = ["npm", "run", "start"]
        shell = False
    
    frontend_process = subprocess.Popen(
        frontend_cmd,
        cwd=str(frontend_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
        shell=shell
    )
    
    # Wait for frontend to start
    print("Waiting for frontend to start...")
    for _ in range(60):  # Wait up to 60 seconds
        line = frontend_process.stdout.readline()
        print(f"Frontend: {line.strip()}")
        if "Compiled successfully" in line or "webpack compiled" in line:
            print("Frontend started successfully!")
            break
        time.sleep(1)
    
    return frontend_process

def start_xvfb():
    """Start Xvfb virtual display server (for Linux only)"""
    if platform.system() != "Linux":
        print("Xvfb is only needed on Linux, skipping on this platform")
        return None
    
    print("Starting Xvfb virtual display...")
    try:
        # Start Xvfb on display :99 with resolution 1024x768x24
        xvfb_cmd = ["Xvfb", ":99", "-screen", "0", "1024x768x24", "-ac"]
        xvfb_process = subprocess.Popen(
            xvfb_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        
        # Set the DISPLAY environment variable for child processes
        os.environ["DISPLAY"] = ":99"
        
        # Wait a moment for Xvfb to initialize
        time.sleep(1)
        
        # Check if Xvfb started successfully
        if xvfb_process.poll() is None:
            print("✓ Xvfb started successfully on display :99")
            return xvfb_process
        else:
            print("❌ Failed to start Xvfb")
            return None
    except (subprocess.SubprocessError, FileNotFoundError):
        print("❌ Xvfb not found. Please install Xvfb: sudo apt-get install xvfb")
        return None

def check_dependencies():
    """Check if required dependencies are installed"""
    # Check for Python dependencies
    try:
        import uvicorn
        print("✓ Backend dependencies found")
    except ImportError:
        print("❌ Backend dependencies missing. Please run: py -3 -m pip install -r backend/requirements.txt")
        return False
    
    # Check for npm
    try:
        if os.name == 'nt':  # Windows
            subprocess.run("npm --version", shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        else:
            subprocess.run(["npm", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("✓ npm found")
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        print("❌ npm not found. Please install Node.js and npm: https://nodejs.org/")
        return False
        
    # Check for Xvfb on Linux
    if platform.system() == "Linux":
        try:
            subprocess.run(["which", "Xvfb"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print("✓ Xvfb found")
        except (subprocess.SubprocessError, FileNotFoundError):
            print("❌ Xvfb not found. Please install: sudo apt-get install xvfb")
            return False
            
    return True

def main():
    """Main function to start both backend and frontend"""
    print("Starting the application...")
    
    # Check dependencies first
    if not check_dependencies():
        print("Missing dependencies. Please install them and try again.")
        return
    
    try:
        # Start Xvfb first (on Linux)
        xvfb_process = start_xvfb()
        
        # Update .env file with DISPLAY variable on Linux
        if platform.system() == "Linux" and xvfb_process is not None:
            backend_dir = Path("backend").absolute()
            env_file = backend_dir / ".env"
            
            # Read existing content
            env_content = ""
            if env_file.exists():
                with open(env_file, "r") as f:
                    env_content = f.read()
            
            # Add or update DISPLAY variable
            if "DISPLAY=" not in env_content:
                with open(env_file, "a") as f:
                    f.write("\nDISPLAY=:99\n")
                print("Added DISPLAY=:99 to backend/.env file")
        
        # Start backend
        backend_process = start_backend()
        
        # Start frontend
        frontend_process = start_frontend()
        
        # Print access URLs
        print("\n" + "="*50)
        print("Application is running!")
        print("Backend URL: http://127.0.0.1:8000")
        print("Frontend URL: http://localhost:3000")
        print("="*50 + "\n")
        
        try:
            # Keep the script running until Ctrl+C
            print("Press Ctrl+C to stop the application...")
            while True:
                # Print any output from processes
                backend_line = backend_process.stdout.readline()
                if backend_line:
                    print(f"Backend: {backend_line.strip()}")
                
                frontend_line = frontend_process.stdout.readline()
                if frontend_line:
                    print(f"Frontend: {frontend_line.strip()}")
                
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("Stopping the application...")
            backend_process.terminate()
            frontend_process.terminate()
            if 'xvfb_process' in locals() and xvfb_process is not None:
                xvfb_process.terminate()
            print("Application stopped.")
    except Exception as e:
        print(f"Error starting application: {e}")
        print("Please check that all dependencies are installed and try again.")

if __name__ == "__main__":
    main()