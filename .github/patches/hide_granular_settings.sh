#!/bin/bash
# Script to dynamically hide specific settings tabs based on DECODED_CONFIG
# Compatible with RustDesk 1.4.4 - 1.4.7
# All 7 tabs can be independently controlled:
#   - General (常规设置)
#   - Security (安全设置)
#   - Network (网络设置)
#   - Display (显示设置)
#   - Account (账户设置)
#   - Plugin (插件设置)
#   - Remote Printer (远程打印设置)
#
# Usage: export DECODED_CONFIG='{"hide-security-settings":"Y",...}' && bash hide_granular_settings.sh

echo "=== HIDING GRANULAR SETTINGS (DYNAMIC) ==="

# Use Python for reliable multi-line replacement
python3 << 'PYTHON_SCRIPT'
import re
import os
import json

# Read the decoded config from environment variable
decoded_config_str = os.environ.get('DECODED_CONFIG', '{}')
print(f"DECODED_CONFIG (first 300 chars): {decoded_config_str[:300]}...")

# Parse JSON
try:
    cleaned_str = decoded_config_str.strip()
    if cleaned_str.startswith("'") and cleaned_str.endswith("'"):
        cleaned_str = cleaned_str[1:-1]
    config = json.loads(cleaned_str)
    print("✅ Successfully parsed DECODED_CONFIG")
except json.JSONDecodeError as e:
    print(f"⚠️  Warning: Could not parse DECODED_CONFIG as JSON: {e}")
    print("Will not hide any tabs (fallback to no hiding)")
    config = {}

# Determine which sections to hide based on config
hide_general = config.get('hide-general-settings') == 'Y'
hide_security = config.get('hide-security-settings') == 'Y'
hide_network = config.get('hide-network-settings') == 'Y'
hide_display = config.get('hide-display-settings') == 'Y'
hide_account = config.get('hide-account-settings') == 'Y'
hide_plugin = config.get('hide-plugin-settings') == 'Y'
hide_printer = config.get('hide-remote-printer-settings') == 'Y'

print("\n=== Hide Settings Configuration ===")
print(f"Hide General Settings:   {hide_general}")
print(f"Hide Security Settings:  {hide_security}")
print(f"Hide Network Settings:   {hide_network}")
print(f"Hide Display Settings:   {hide_display}")
print(f"Hide Account Settings:   {hide_account}")
print(f"Hide Plugin Settings:    {hide_plugin}")
print(f"Hide Printer Settings:   {hide_printer}")
print("================================\n")

# Function to safely modify file with backup
def modify_file_with_backup(filepath, modifier_func):
    if not os.path.exists(filepath):
        print(f"⚠️  {filepath} not found, skipping")
        return False
    
    with open(filepath, 'r', encoding='utf-8') as f:
        original_content = f.read()
    
    new_content = modifier_func(original_content)
    
    if new_content != original_content:
        # Create backup only if changes were made
        backup_path = filepath + ".bak"
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(original_content)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"✅ Modified {filepath}")
        return True
    else:
        print(f"ℹ️  No changes needed for {filepath}")
        return False

# Process Desktop file
desktop_file = "flutter/lib/desktop/pages/desktop_setting_page.dart"

def modify_desktop_settings(content):
    modified = False
    
    # Helper function to comment out a line or block
    def hide_line(pattern, comment_prefix="// HIDDEN: "):
        nonlocal content, modified
        if re.search(pattern, content, re.MULTILINE):
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                if re.search(pattern, line) and not line.strip().startswith('//'):
                    new_lines.append(f"// {comment_prefix}{line}")
                    modified = True
                else:
                    new_lines.append(line)
            content = '\n'.join(new_lines)
            return True
        return False
    
    # 1. Hide General Settings
    if hide_general:
        # Pattern for SettingsTabKey.general
        pattern = r'^\s*SettingsTabKey\.general,'
        if re.search(pattern, content, re.MULTILINE):
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                if re.search(pattern, line) and not line.strip().startswith('//'):
                    new_lines.append(f"// GENERAL SETTINGS HIDDEN: {line}")
                    modified = True
                else:
                    new_lines.append(line)
            content = '\n'.join(new_lines)
            print("  ✓ Hid General settings")
        else:
            print("  ⚠️  General settings pattern not found")
    
    # 2. Hide Security Settings
    if hide_security:
        pattern = r'^\s*if\s*\([^)]*kOptionHideSecuritySetting[^)]*\)\s*SettingsTabKey\.safety,'
        if re.search(pattern, content, re.MULTILINE):
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                if re.search(pattern, line) and not line.strip().startswith('//'):
                    new_lines.append(f"// SECURITY SETTINGS HIDDEN: {line}")
                    modified = True
                else:
                    new_lines.append(line)
            content = '\n'.join(new_lines)
            print("  ✓ Hid Security settings")
        else:
            print("  ⚠️  Security settings pattern not found")
    
    # 3. Hide Network Settings
    if hide_network:
        pattern = r'^\s*if\s*\([^)]*kOptionHideNetworkSetting[^)]*\)\s*SettingsTabKey\.network,'
        if re.search(pattern, content, re.MULTILINE):
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                if re.search(pattern, line) and not line.strip().startswith('//'):
                    new_lines.append(f"// NETWORK SETTINGS HIDDEN: {line}")
                    modified = True
                else:
                    new_lines.append(line)
            content = '\n'.join(new_lines)
            print("  ✓ Hid Network settings")
        else:
            print("  ⚠️  Network settings pattern not found")
    
    # 4. Hide Display Settings
    if hide_display:
        pattern = r'^\s*if\s*\(\s*!bind\.isIncomingOnly\(\)\s*\)\s*SettingsTabKey\.display,'
        if re.search(pattern, content, re.MULTILINE):
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                if re.search(pattern, line) and not line.strip().startswith('//'):
                    new_lines.append(f"// DISPLAY SETTINGS HIDDEN: {line}")
                    modified = True
                else:
                    new_lines.append(line)
            content = '\n'.join(new_lines)
            print("  ✓ Hid Display settings")
        else:
            print("  ⚠️  Display settings pattern not found")
    
    # 5. Hide Account Settings
    if hide_account:
        pattern = r'^\s*if\s*\(\s*!bind\.isDisableAccount\(\)\s*\)\s*SettingsTabKey\.account,'
        if re.search(pattern, content, re.MULTILINE):
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                if re.search(pattern, line) and not line.strip().startswith('//'):
                    new_lines.append(f"// ACCOUNT SETTINGS HIDDEN: {line}")
                    modified = True
                else:
                    new_lines.append(line)
            content = '\n'.join(new_lines)
            print("  ✓ Hid Account settings")
        else:
            print("  ⚠️  Account settings pattern not found")
    
    # 6. Hide Plugin Settings
    if hide_plugin:
        pattern = r'^\s*if\s*\([^)]*pluginFeatureIsEnabled\(\)[^)]*\)\s*SettingsTabKey\.plugin,'
        if re.search(pattern, content, re.MULTILINE):
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                if re.search(pattern, line) and not line.strip().startswith('//'):
                    new_lines.append(f"// PLUGIN SETTINGS HIDDEN: {line}")
                    modified = True
                else:
                    new_lines.append(line)
            content = '\n'.join(new_lines)
            print("  ✓ Hid Plugin settings")
        else:
            print("  ⚠️  Plugin settings pattern not found")
    
    # 7. Hide Remote Printer Settings
    if hide_printer:
        pattern = r'^\s*if\s*\([^)]*kOptionHideRemotePrinterSetting[^)]*\)\s*SettingsTabKey\.printer,'
        if re.search(pattern, content, re.MULTILINE):
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                if re.search(pattern, line) and not line.strip().startswith('//'):
                    new_lines.append(f"// PRINTER SETTINGS HIDDEN: {line}")
                    modified = True
                else:
                    new_lines.append(line)
            content = '\n'.join(new_lines)
            print("  ✓ Hid Printer settings")
        else:
            print("  ⚠️  Printer settings pattern not found")
    
    return content

# Apply modifications
print(f"Processing {desktop_file}...")
if os.path.exists(desktop_file):
    modify_file_with_backup(desktop_file, modify_desktop_settings)
else:
    print(f"❌ {desktop_file} not found!")

print("\n✅ Granular settings hiding complete!")
PYTHON_SCRIPT

echo "=== GRANULAR SETTINGS HIDING COMPLETE ==="
