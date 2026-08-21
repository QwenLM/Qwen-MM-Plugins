import os
import sys
import shutil
import argparse
from pathlib import Path

# --- Configuration & State ---
REPO_URL = os.environ.get("QMP_REPO", "https://github.com/QwenLM/Qwen-MM-Plugins.git")
REPO_REF = os.environ.get("QMP_REF", "")
MARKETPLACE = "qwen-mm-plugins"

CONFIG_DIR = Path(os.environ.get("QWEN_MM_CONFIG_DIR", Path.home() / ".qwen-mm-plugins"))
CONFIG_FILE = Path(os.environ.get("QWEN_MM_CONFIG", CONFIG_DIR / "config"))
OPENCLAW_MARKETPLACE_DIR = Path(os.environ.get("QMP_OPENCLAW_MARKETPLACE_DIR", CONFIG_DIR / "openclaw-marketplace"))

# --- Catalogs ---
CAPABILITIES = [
    {"name": "core",         "version": "1.0.1", "desc": "read/visualize any local file — images, video, docs, 3D", "skill_only": False},
    {"name": "api",          "version": "1.0.1", "desc": "cloud media APIs by model family: VL (vision_chat/ocr/grounding), Omni A/V, ASR, segmentation", "skill_only": False},
    {"name": "search",       "version": "1.0.2", "desc": "web search/extraction (Serper, Exa, Tavily) + Serper reverse-image search", "skill_only": False},
    {"name": "video-memory", "version": "1.0.1", "desc": "hierarchical graph memory for long-video QA", "skill_only": False},
    {"name": "video-edit",   "version": "1.0.1", "desc": "video-edit + image/video/audio generation", "skill_only": False},
    {"name": "blender",      "version": "1.0.1", "desc": "drive a running Blender: 3D modeling / materials / render (thin client)", "skill_only": False},
    {"name": "freecad",      "version": "1.0.1", "desc": "drive a running FreeCAD: parametric CAD / STEP·STL / FEM (thin client)", "skill_only": False},
    {"name": "edu-agent",    "version": "1.0.1", "desc": "step-by-step Chinese math/science tutorial videos (skill-only)", "skill_only": True}
]

HARNESSES = {
    "marketplace": ["claude", "codex", "qoder", "openclaw"],
    "config": ["qwen-code", "gemini"]
}

def get_harness_bin(harness: str) -> str:
    if harness == "qoder": return "qodercli"
    if harness == "qwen-code": return "qwen"
    return harness

def is_harness_installed(harness: str) -> bool:
    bin_name = get_harness_bin(harness)
    return shutil.which(bin_name) is not None

def check_cfg_has(harness: str, cap_name: str) -> bool:
    plugin_id = f"qwen-mm-plugins-{cap_name}"
    home = Path.home()
    if harness == "qwen-code":
        ext_dir = home / ".qwen" / "extensions" / plugin_id
        settings = home / ".qwen" / "settings.json"
        if ext_dir.is_dir(): return True
        if settings.is_file() and f'"{plugin_id}"' in settings.read_text(encoding='utf-8', errors='ignore'): return True
    elif harness == "gemini":
        ext_dir = home / ".gemini" / "extensions" / plugin_id
        skill_dir = home / ".gemini" / "skills" / plugin_id
        settings = home / ".gemini" / "settings.json"
        if ext_dir.is_dir() or skill_dir.is_dir(): return True
        if settings.is_file() and f'"{plugin_id}"' in settings.read_text(encoding='utf-8', errors='ignore'): return True
    return False

def detect_harness_mask(harness: str) -> list[bool]:
    import subprocess
    installed = [False] * len(CAPABILITIES)
    if not is_harness_installed(harness):
        return installed

    if harness in HARNESSES["config"]:
        for i, cap in enumerate(CAPABILITIES):
            installed[i] = check_cfg_has(harness, cap["name"])
        return installed

    bin_name = get_harness_bin(harness)
    cmd = [bin_name, "plugins" if harness in ["qoder", "openclaw"] else "plugin", "list"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        out = result.stdout
    except Exception:
        out = ""

    for i, cap in enumerate(CAPABILITIES):
        plugin_id = f"qwen-mm-plugins-{cap['name']}"
        if harness == "codex":
            for line in out.splitlines():
                if f"{plugin_id}@{MARKETPLACE}" in line and "not installed" not in line:
                    installed[i] = True
                    break
        elif harness == "openclaw":
            if plugin_id in out: installed[i] = True
        else: # claude, qoder
            if f"{plugin_id}@{MARKETPLACE}" in out: installed[i] = True

    return installed

def cap_spec(cap: str) -> str:
    # Basic cap_spec logic. For local repos, it points to the local path.
    # We will assume remote for simplicity unless REPO_URL is file://
    if REPO_URL.startswith("file://"):
        return f"qwen-mm-plugins[{cap}] @ {REPO_URL}"
    # Just returning the pip string
    ref = REPO_REF if REPO_REF else f"qwen-mm-plugins-{cap}-v{next(c['version'] for c in CAPABILITIES if c['name'] == cap)}"
    return f"qwen-mm-plugins[{cap}] @ git+{REPO_URL}@{ref}"

def install_for(harness: str, plugins: list[str]):
    import subprocess
    bin_name = get_harness_bin(harness)
    if not shutil.which(bin_name):
        print(f"[{harness}] CLI not found. Skipping.")
        return False
        
    print(f"\nInstalling for {harness}...")
    success = True
    
    for plugin in plugins:
        plugin_id = f"qwen-mm-plugins-{plugin}"
        try:
            if harness == "claude":
                subprocess.run([bin_name, "plugin", "marketplace", "add", REPO_URL], check=False)
                subprocess.run([bin_name, "plugin", "install", f"{plugin_id}@{MARKETPLACE}"], check=True)
            elif harness == "codex":
                subprocess.run([bin_name, "plugin", "marketplace", "add", REPO_URL], check=False)
                subprocess.run([bin_name, "plugin", "add", f"{plugin_id}@{MARKETPLACE}"], check=True)
            elif harness == "qoder":
                subprocess.run([bin_name, "plugins", "marketplace", "add", REPO_URL], check=False)
                subprocess.run([bin_name, "plugins", "install", f"{plugin_id}@{MARKETPLACE}"], check=True)
            elif harness == "qwen-code":
                subprocess.run([bin_name, "extensions", "install", f"{REPO_URL}:{plugin_id}", "--consent"], check=True)
            elif harness == "gemini":
                # For gemini we use uvx
                subprocess.run([bin_name, "mcp", "add", "-s", "user", plugin_id, "uvx", "--from", cap_spec(plugin), plugin_id], check=True)
                # Skill install omitted for brevity in MVP
            else:
                print(f"[{harness}] Installation logic not fully implemented in python script.")
        except subprocess.CalledProcessError as e:
            print(f"[{harness}] Failed to install {plugin}: {e}")
            success = False
            
    return success

def update_for(harness: str, plugins: list[str]):
    import subprocess
    bin_name = get_harness_bin(harness)
    if not shutil.which(bin_name):
        print(f"[{harness}] CLI not found. Skipping.")
        return False
        
    print(f"\nUpdating for {harness}...")
    success = True
    
    for plugin in plugins:
        plugin_id = f"qwen-mm-plugins-{plugin}"
        try:
            if harness == "claude":
                subprocess.run([bin_name, "plugin", "update", f"{plugin_id}@{MARKETPLACE}"], check=True)
            elif harness == "codex":
                # codex uses 'add' for updates idempotently
                subprocess.run([bin_name, "plugin", "add", f"{plugin_id}@{MARKETPLACE}"], check=True)
            elif harness == "qoder":
                subprocess.run([bin_name, "plugins", "update", f"{plugin_id}@{MARKETPLACE}"], check=True)
            elif harness == "qwen-code":
                subprocess.run([bin_name, "extensions", "uninstall", plugin_id], check=False)
                subprocess.run([bin_name, "extensions", "install", f"{REPO_URL}:{plugin_id}", "--consent"], check=True)
            elif harness == "gemini":
                subprocess.run([bin_name, "mcp", "add", "-s", "user", plugin_id, "uvx", "--refresh", "--from", cap_spec(plugin), plugin_id], check=True)
            elif harness == "openclaw":
                subprocess.run([bin_name, "plugins", "update", plugin_id], check=True)
            else:
                print(f"[{harness}] Update logic not fully implemented.")
        except subprocess.CalledProcessError as e:
            print(f"[{harness}] Failed to update {plugin}: {e}")
            success = False
            
    return success

def do_configure():
    print("\n--- Configuration ---")
    keys = ["DASHSCOPE_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY"]
    
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    current_config = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r") as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    current_config[k] = v
                    
    for key in keys:
        current_val = current_config.get(key, "Not set")
        if len(current_val) > 10 and current_val != "Not set":
            current_val = f"{current_val[:4]}...{current_val[-4:]}"
        print(f"Current {key}: {current_val}")
        new_val = input(f"Enter new {key} (leave blank to keep current): ").strip()
        if new_val:
            current_config[key] = new_val
            
    with open(CONFIG_FILE, "w") as f:
        f.write("# Qwen-MM-Plugins Config\n")
        for k, v in current_config.items():
            f.write(f"{k}={v}\n")
    print(f"\nConfiguration saved to {CONFIG_FILE}")

def do_verify():
    print("\n--- Verify System Dependencies ---")
    tools = {
        "uvx": "Required for running MCP servers",
        "ffmpeg": "Required for audio/video processing",
        "git": "Required for cloning repositories"
    }
    for tool, desc in tools.items():
        if shutil.which(tool):
            print(f"[OK] {tool: <10} - {desc}")
        else:
            print(f"[MISSING] {tool: <10} - {desc}")

def do_uninstall(harness: str, plugins: list[str]):
    import subprocess
    print(f"\nUninstalling from {harness}...")
    bin_name = get_harness_bin(harness)
    if not shutil.which(bin_name):
        return False
        
    for plugin in plugins:
        plugin_id = f"qwen-mm-plugins-{plugin}"
        try:
            if harness == "claude":
                subprocess.run([bin_name, "plugin", "remove", f"{plugin_id}@{MARKETPLACE}"], check=True)
            elif harness in ["codex", "qoder"]:
                subprocess.run([bin_name, "plugin" if harness == "codex" else "plugins", "uninstall", f"{plugin_id}@{MARKETPLACE}"], check=True)
            elif harness == "openclaw":
                subprocess.run([bin_name, "plugins", "uninstall", plugin_id], check=True)
            elif harness == "qwen-code":
                subprocess.run([bin_name, "extensions", "uninstall", plugin_id], check=True)
            elif harness == "gemini":
                subprocess.run([bin_name, "mcp", "remove", plugin_id], check=True)
            else:
                print(f"[{harness}] Uninstall not fully implemented.")
        except Exception as e:
            print(f"Failed to uninstall {plugin}: {e}")

def get_all_harnesses():
    return HARNESSES["marketplace"] + HARNESSES["config"]

def main():
    parser = argparse.ArgumentParser(description="Qwen-MM-Plugins Installer")
    parser.add_argument("action", nargs="?", choices=["install", "update", "local", "configure", "verify", "uninstall"], 
                        help="The action to perform. If omitted, opens the interactive menu.")
    
    args = parser.parse_args()

    if args.action:
        print(f"Action '{args.action}' selected (CLI mode).")
        # Route to respective handler
    else:
        run_interactive_menu()

def run_interactive_menu():
    print("\n" + "="*50)
    print(" Qwen-MM-Plugins Installer (Python Cross-Platform)")
    print("="*50)
    print("\nSelect an action:")
    print("  1) Install     - Install a release version")
    print("  2) Update      - Update an existing install")
    print("  3) Configure   - Set API keys and settings")
    print("  4) Verify      - Check system dependencies")
    print("  5) Uninstall   - Remove capabilities")
    print("  6) Detect      - [DEV] Test Harness Detection")
    print("  0) Exit")
    
    choice = input("\nEnter choice (0-6): ").strip()
    
    if choice == '1':
        print("\n--- Install Flow ---")
        harnesses_present = [h for h in get_all_harnesses() if is_harness_installed(h)]
        if not harnesses_present:
            print("No supported AI assistants found on your system.")
            return

        print("Detected assistants:")
        for i, h in enumerate(harnesses_present):
            print(f"  {i+1}) {h}")
        
        h_choice = input(f"Select assistant (1-{len(harnesses_present)}): ").strip()
        try:
            selected_harness = harnesses_present[int(h_choice)-1]
        except (ValueError, IndexError):
            print("Invalid choice.")
            return
            
        print("\nAvailable plugins:")
        for i, cap in enumerate(CAPABILITIES):
            print(f"  {i+1}) {cap['name']} - {cap['desc']}")
            
        p_choice = input("Enter plugin number(s) to install (e.g. '1 2'): ").strip()
        selected_plugins = []
        for num in p_choice.split():
            try:
                selected_plugins.append(CAPABILITIES[int(num)-1]["name"])
            except (ValueError, IndexError):
                print(f"Invalid plugin number: {num}")
                
        if selected_plugins:
            install_for(selected_harness, selected_plugins)
        else:
            print("No plugins selected.")
            
    elif choice == '2':
        print("\n--- Update Flow ---")
        harnesses_present = [h for h in get_all_harnesses() if is_harness_installed(h)]
        if not harnesses_present:
            print("No supported AI assistants found.")
            return

        for i, h in enumerate(harnesses_present):
            print(f"  {i+1}) {h}")
        
        h_choice = input(f"Select assistant to update (1-{len(harnesses_present)}): ").strip()
        try:
            selected_harness = harnesses_present[int(h_choice)-1]
        except: return
        
        # In a complete implementation we'd filter only INSTALLED plugins.
        # For simplicity, we ask which ones to update.
        print("\nAvailable plugins:")
        for i, cap in enumerate(CAPABILITIES):
            print(f"  {i+1}) {cap['name']}")
        p_choice = input("Enter plugin number(s) to update (e.g. '1 2'): ").strip()
        selected_plugins = [CAPABILITIES[int(num)-1]["name"] for num in p_choice.split() if num.isdigit()]
        if selected_plugins:
            update_for(selected_harness, selected_plugins)
            
    elif choice == '3':
        do_configure()
        
    elif choice == '4':
        do_verify()
        
    elif choice == '5':
        print("\n--- Uninstall Flow ---")
        harnesses_present = [h for h in get_all_harnesses() if is_harness_installed(h)]
        for i, h in enumerate(harnesses_present): print(f"  {i+1}) {h}")
        h_choice = input(f"Select assistant (1-{len(harnesses_present)}): ").strip()
        try: selected_harness = harnesses_present[int(h_choice)-1]
        except: return
        
        for i, cap in enumerate(CAPABILITIES): print(f"  {i+1}) {cap['name']}")
        p_choice = input("Enter plugin number(s) to uninstall: ").strip()
        selected_plugins = [CAPABILITIES[int(num)-1]["name"] for num in p_choice.split() if num.isdigit()]
        if selected_plugins:
            do_uninstall(selected_harness, selected_plugins)
            
    elif choice == '6':
        print("\n--- Harness Detection Test ---")
        for h in get_all_harnesses():
            if is_harness_installed(h):
                print(f"[OK] {h} is installed.")
                mask = detect_harness_mask(h)
                for i, cap in enumerate(CAPABILITIES):
                    if mask[i]:
                        print(f"     -> Plugin '{cap['name']}' is INSTALLED.")
            else:
                print(f"[  ] {h} is not found on PATH.")
    elif choice == '0':
        sys.exit(0)
    else:
        print("Not yet implemented in Python.")

if __name__ == "__main__":
    main()
