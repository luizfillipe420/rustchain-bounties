#!/usr/bin/env python3
"""
RustChain CLI — Command-Line Network Inspector
==============================================

A lightweight, zero-dependency command-line tool for querying the RustChain network.
Abstracts raw REST API responses into a polished, table-driven terminal interface.

README & Usage Examples
-----------------------
Installation:
    chmod +x rustchain_cli.py
    sudo mv rustchain_cli.py /usr/local/bin/rustchain-cli

    # Bonus: Bash Completion
    rustchain-cli --generate-completion > ~/.rustchain-completion.sh
    source ~/.rustchain-completion.sh

Usage:
    rustchain-cli status
    rustchain-cli miners
    rustchain-cli miners --count
    rustchain-cli balance 0x123abc
    rustchain-cli balance --all
    rustchain-cli epoch
    rustchain-cli epoch history
    rustchain-cli hall --category exotic
    rustchain-cli fees

Global Options:
    --node       URL of the RustChain node (default: https://rustchain.org)
                 Can also be set via RUSTCHAIN_NODE environment variable.
    --json       Output raw JSON instead of formatted tables.
    --no-color   Disable ANSI terminal color output.

PR to: Scottcjn/Rustchain (tools/cli/)
"""

import sys
import os
import json
import argparse
import urllib.request
import urllib.error
import ssl

# ==========================================
# Utilities: Data Extraction & Normalization
# ==========================================

def flatten_dict(d, parent_key='', sep='_'):
    """Flatten a nested dictionary for easier table rendering."""
    items = []
    if not isinstance(d, dict):
        return {parent_key: d}
    
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        elif isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
            items.append((new_key, f"<List of {len(v)} items>"))
        else:
            items.append((new_key, v))
    return dict(items)

def extract_list(data):
    """Intelligently extract the primary list from a generic API response."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Look for the first list in dict (e.g., {"miners": [...], "count": 10})
        for k, v in data.items():
            if isinstance(v, list):
                return v
    return [data]

def normalize_dict(d):
    """Stringify all values in a flattened dict."""
    res = {}
    for k, v in flatten_dict(d).items():
        res[k] = str(v)
    return res

# ==========================================
# Client & Formatter
# ==========================================

class RustChainClient:
    """Zero-dependency HTTP client for RustChain API."""
    def __init__(self, node_url):
        self.node_url = node_url.rstrip('/')
        
        # Bypass SSL verification to support self-signed nodes seamlessly (like curl -k)
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

    def get(self, path):
        url = f"{self.node_url}/{path.lstrip('/')}"
        req = urllib.request.Request(url, headers={'User-Agent': 'RustChain-CLI/1.0.0'})
        try:
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=10) as response:
                body = response.read().decode('utf-8')
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode('utf-8')
                return json.loads(body)
            except Exception:
                return {"error": f"HTTP {e.code}: {e.reason}"}
        except Exception as e:
            return {"error": f"Connection failed: {str(e)}"}


class Formatter:
    """Handles terminal output, table rendering, and coloring."""
    def __init__(self, json_mode=False, color_mode=True):
        self.json = json_mode
        self.color = color_mode and sys.stdout.isatty()

        self.C_HEAD = '\033[96m\033[1m'   # Cyan Bold
        self.C_KEY  = '\033[93m'          # Yellow
        self.C_VAL  = '\033[97m'          # White
        self.C_ERR  = '\033[91m\033[1m'   # Red Bold
        self.C_SUC  = '\033[92m'          # Green
        self.C_END  = '\033[0m'

    def _c(self, text, color_code):
        return f"{color_code}{text}{self.C_END}" if self.color else str(text)

    def print_json(self, data):
        print(json.dumps(data, indent=2))
        
    def print_error(self, msg):
        print(self._c(f"ERROR: {msg}", self.C_ERR), file=sys.stderr)

    def print_result(self, msg):
        if self.json:
            self.print_json({"result": msg})
        else:
            print(self._c(msg, self.C_SUC))

    def print_vertical_table(self, title, data):
        """Prints a single dictionary as a vertical Key-Value table."""
        if self.json:
            self.print_json(data)
            return
            
        if not isinstance(data, dict):
            data = {"value": data}

        if "error" in data:
            self.print_error(data["error"])
            return

        print(self._c(f"=== {title} ===", self.C_HEAD))
        flat = flatten_dict(data)
        if not flat:
            print("No data available.\n")
            return
            
        max_key = max((len(str(k)) for k in flat.keys()), default=0)
        for k, v in flat.items():
            k_str = str(k).replace('_', ' ').title()
            print(f"{self._c(k_str.ljust(max_key), self.C_KEY)} : {self._c(v, self.C_VAL)}")
        print()

    def print_table(self, title, items, preferred_keys=None):
        """Prints a list of dictionaries as a horizontal formatted table."""
        if self.json:
            self.print_json(items)
            return
            
        if isinstance(items, dict) and "error" in items:
            self.print_error(items["error"])
            return

        print(self._c(f"=== {title} ===", self.C_HEAD))
        if not items:
            print("No records found.\n")
            return

        norm_items = [normalize_dict(i) if isinstance(i, dict) else {'Value': str(i)} for i in items]
        
        # Discover all available keys
        all_keys = []
        for item in norm_items:
            for k in item.keys():
                if k not in all_keys:
                    all_keys.append(k)
                    
        # Filter and order columns
        display_keys = []
        if preferred_keys:
            # Fuzzy match preferred keys
            for pk in preferred_keys:
                matches = [k for k in all_keys if pk.lower() in k.lower()]
                for m in matches:
                    if m not in display_keys:
                        display_keys.append(m)
            # Add a few leftover keys if the preferred ones didn't match much
            if len(display_keys) < len(preferred_keys):
                for k in all_keys[:5]:
                    if k not in display_keys:
                        display_keys.append(k)
        else:
            display_keys = all_keys[:6]

        if not display_keys:
            print("No displayable data.\n")
            return

        # Calculate column widths
        widths = {k: len(k.replace('_', ' ').title()) for k in display_keys}
        for item in norm_items:
            for k in display_keys:
                widths[k] = max(widths[k], len(str(item.get(k, '-'))))

        # Render Header
        headers = [self._c(k.replace('_', ' ').title().ljust(widths[k]), self.C_KEY) for k in display_keys]
        print(" | ".join(headers))
        
        # Render Separator
        separators = ["-" * widths[k] for k in display_keys]
        print("-+-".join(separators))
        
        # Render Rows
        for item in norm_items:
            row = [self._c(str(item.get(k, '-')).ljust(widths[k]), self.C_VAL) for k in display_keys]
            print(" | ".join(row))
        print()


# ==========================================
# Command Handlers
# ==========================================

def cmd_status(args, client, fmt):
    data = client.get("/health")
    fmt.print_vertical_table("RustChain Node Status", data)

def cmd_miners(args, client, fmt):
    data = client.get("/api/miners")
    miners_list = extract_list(data)
    
    if args.count:
        count = data.get('count') if isinstance(data, dict) and 'count' in data else len(miners_list)
        fmt.print_result(f"Active Miners Count: {count}")
    else:
        fmt.print_table("Active Miners", miners_list, preferred_keys=['id', 'miner', 'arch', 'last_attest', 'uptime'])

def cmd_balance(args, client, fmt):
    if args.all:
        # Try specific balance endpoint or fallback to miners list
        data = client.get("/balance")
        items = extract_list(data)
        if not items or len(items) == 0 or (len(items) == 1 and "error" in items[0]):
            items = extract_list(client.get("/api/miners"))
            
        # Dynamically find the balance key
        bal_key = 'balance'
        for item in items:
            for k in item.keys():
                if 'bal' in k.lower():
                    bal_key = k
                    break

        def get_bal(x):
            try:
                return float(x.get(bal_key, 0))
            except:
                return 0.0
                
        items = sorted(items, key=get_bal, reverse=True)[:10]
        fmt.print_table("Top 10 Balances", items, preferred_keys=['id', 'miner', bal_key])
        
    else:
        if not args.miner_id:
            fmt.print_error("Miner ID is required unless using --all")
            sys.exit(1)
            
        data = client.get(f"/balance/{args.miner_id}")
        if "error" in data and "404" in data["error"]:
            # Fallback to query param structure if standard path fails
            data = client.get(f"/balance?miner_id={args.miner_id}")
            
        fmt.print_vertical_table(f"Wallet Balance: {args.miner_id}", data)

def cmd_epoch(args, client, fmt):
    if args.subcmd == 'history':
        data = client.get("/epoch/history")
        if "error" in data or not extract_list(data):
            # Fallback to base /epoch to look for nested history
            base_data = client.get("/epoch")
            data = base_data.get("history", base_data.get("settlements", []))
            
        items = extract_list(data)[:5]
        fmt.print_table("Epoch Settlement History (Last 5)", items, preferred_keys=['epoch', 'settled', 'hash', 'validator'])
    else:
        data = client.get("/epoch")
        # Remove massive history lists from the status output
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if not isinstance(v, list)}
        fmt.print_vertical_table("Current Epoch Information", data)

def cmd_hall(args, client, fmt):
    data = client.get("/api/hall_of_fame")
    category = args.category.lower()
    
    items = []
    if isinstance(data, dict):
        # Look for the specific category key, handling underscores/spaces
        cat_key = next((k for k in data.keys() if category in k.lower().replace('_', ' ')), None)
        if cat_key:
            items = data[cat_key]
        else:
            items = extract_list(data)
    else:
        items = extract_list(data)
        
    # Apply category filter manually if it wasn't pre-grouped
    filtered_items = []
    for item in items:
        if isinstance(item, dict):
            item_cat = str(item.get('category', '')).lower()
            if category in item_cat or not item_cat:
                filtered_items.append(item)
        else:
            filtered_items.append(item)
            
    if not filtered_items:
        filtered_items = items
        
    fmt.print_table(f"Hall of Fame - {category.title()} (Top 5)", filtered_items[:5], preferred_keys=['id', 'miner', 'score', 'arch', 'blocks'])

def cmd_fees(args, client, fmt):
    data = client.get("/api/fee_pool")
    fmt.print_vertical_table("RIP-301 Fee Pool Statistics", data)


# ==========================================
# Shell Completion Bonus
# ==========================================

def generate_bash_completion():
    script = """
# RustChain CLI Bash Completion
_rustchain_cli() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    opts="status miners balance epoch hall fees --node --json --no-color"
    
    case "${prev}" in
        miners)
            COMPREPLY=( $(compgen -W "--count" -- ${cur}) )
            return 0
            ;;
        balance)
            COMPREPLY=( $(compgen -W "--all" -- ${cur}) )
            return 0
            ;;
        epoch)
            COMPREPLY=( $(compgen -W "history" -- ${cur}) )
            return 0
            ;;
        hall)
            COMPREPLY=( $(compgen -W "--category" -- ${cur}) )
            return 0
            ;;
        --category)
            COMPREPLY=( $(compgen -W "ancient_iron exotic standard" -- ${cur}) )
            return 0
            ;;
    esac
    
    COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
}
complete -F _rustchain_cli rustchain-cli rustchain_cli.py
"""
    print(script.strip())
    sys.exit(0)


# ==========================================
# Main Entry Point
# ==========================================

def main():
    if "--generate-completion" in sys.argv:
        generate_bash_completion()

    parser = argparse.ArgumentParser(
        description="RustChain CLI — Command-Line Network Inspector",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    parser.add_argument('--node', 
                        default=os.environ.get('RUSTCHAIN_NODE', 'https://rustchain.org'), 
                        help='Target node URL (default: https://rustchain.org)')
    parser.add_argument('--json', action='store_true', help='Output in machine-readable JSON format')
    parser.add_argument('--no-color', action='store_true', help='Disable colored terminal output')
    parser.add_argument('--generate-completion', action='store_true', help=argparse.SUPPRESS)

    subparsers = parser.add_subparsers(dest='command', help='Available commands', required=True)
    
    # rustchain-cli status
    subparsers.add_parser('status', help='View Node health, version, uptime, and epoch')
    
    # rustchain-cli miners [--count]
    p_miners = subparsers.add_parser('miners', help='List active miners with architecture and last attestation')
    p_miners.add_argument('--count', action='store_true', help='Return only the total count of active miners')
    
    # rustchain-cli balance <miner_id> | --all
    p_balance = subparsers.add_parser('balance', help='Check wallet balances')
    p_balance.add_argument('miner_id', nargs='?', help='Specific Miner ID to check')
    p_balance.add_argument('--all', action='store_true', help='Show top 10 highest balances')
    
    # rustchain-cli epoch [history]
    p_epoch = subparsers.add_parser('epoch', help='Current epoch, slot, and countdown details')
    p_epoch.add_argument('subcmd', nargs='?', choices=['history'], help='Show the last 5 epoch settlements')
    
    # rustchain-cli hall [--category exotic]
    p_hall = subparsers.add_parser('hall', help='Hall of Fame rankings')
    p_hall.add_argument('--category', default='ancient_iron', help='Filter by category (e.g. exotic, ancient_iron)')
    
    # rustchain-cli fees
    subparsers.add_parser('fees', help='View RIP-301 fee pool network statistics')

    args = parser.parse_args()

    client = RustChainClient(args.node)
    fmt = Formatter(json_mode=args.json, color_mode=not args.no_color)

    # Route to handler
    handlers = {
        'status': cmd_status,
        'miners': cmd_miners,
        'balance': cmd_balance,
        'epoch': cmd_epoch,
        'hall': cmd_hall,
        'fees': cmd_fees
    }

    try:
        handlers[args.command](args, client, fmt)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(130)
    except Exception as e:
        fmt.print_error(f"Unexpected error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()