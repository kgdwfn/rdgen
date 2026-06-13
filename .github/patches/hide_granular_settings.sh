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

python3 << 'PYTHON_SCRIPT'
import re
import os
import json

decoded_config_str = os.environ.get('DECODED_CONFIG', '{}')
print(f"DECODED_CONFIG (first 300 chars): {decoded_config_str[:300]}...")

try:
    cleaned_str = decoded_config_str.strip()
    if cleaned_str.startswith("'") and cleaned_str.endswith("'"):
        cleaned_str = cleaned_str[1:-1]
    config = json.loads(cleaned_str)
    print("✅ Successfully parsed DECODED_CONFIG")
except json.JSONDecodeError as e:
    print(f"⚠️  Warning: Could not parse DECODED_CONFIG as JSON: {e}")
    config = {}

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

def modify_file_with_backup(filepath, modifier_func):
    if not os.path.exists(filepath):
        print(f"⚠️  {filepath} not found, skipping")
        return False
    
    with open(filepath, 'r', encoding='utf-8') as f:
        original_content = f.read()
    
    new_content = modifier_func(original_content)
    
    if new_content != original_content:
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

desktop_file = "flutter/lib/desktop/pages/desktop_setting_page.dart"

def modify_desktop_settings(content):
    modified = False
    lines = content.split('\n')
    new_lines = []
    
    for line in lines:
        new_line = line
        
        # Hide General Settings
        if hide_general and re.search(r'^\s*SettingsTabKey\.general,', line):
            if not line.strip().startswith('//'):
                new_line = f"// GENERAL SETTINGS HIDDEN: {line}"
                modified = True
                print("  ✓ Hid General settings")
        
        # Hide Security Settings
        if hide_security and re.search(r'^\s*if\s*\([^)]*kOptionHideSecuritySetting[^)]*\)\s*SettingsTabKey\.safety,', line):
            if not line.strip().startswith('//'):
                new_line = f"// SECURITY SETTINGS HIDDEN: {line}"
                modified = True
                print("  ✓ Hid Security settings")
        
        # Hide Network Settings
        if hide_network and re.search(r'^\s*if\s*\([^)]*kOptionHideNetworkSetting[^)]*\)\s*SettingsTabKey\.network,', line):
            if not line.strip().startswith('//'):
                new_line = f"// NETWORK SETTINGS HIDDEN: {line}"
                modified = True
                print("  ✓ Hid Network settings")
        
        # Hide Display Settings
        if hide_display and re.search(r'^\s*if\s*\(\s*!bind\.isIncomingOnly\(\)\s*\)\s*SettingsTabKey\.display,', line):
            if not line.strip().startswith('//'):
                new_line = f"// DISPLAY SETTINGS HIDDEN: {line}"
                modified = True
                print("  ✓ Hid Display settings")
        
        # Hide Account Settings
        if hide_account and re.search(r'^\s*if\s*\(\s*!bind\.isDisableAccount\(\)\s*\)\s*SettingsTabKey\.account,', line):
            if not line.strip().startswith('//'):
                new_line = f"// ACCOUNT SETTINGS HIDDEN: {line}"
                modified = True
                print("  ✓ Hid Account settings")
        
        # Hide Plugin Settings
        if hide_plugin and re.search(r'^\s*if\s*\([^)]*pluginFeatureIsEnabled\(\)[^)]*\)\s*SettingsTabKey\.plugin,', line):
            if not line.strip().startswith('//'):
                new_line = f"// PLUGIN SETTINGS HIDDEN: {line}"
                modified = True
                print("  ✓ Hid Plugin settings")
        
        # Hide Remote Printer Settings
        if hide_printer and re.search(r'^\s*if\s*\([^)]*kOptionHideRemotePrinterSetting[^)]*\)\s*SettingsTabKey\.printer,', line):
            if not line.strip().startswith('//'):
                new_line = f"// PRINTER SETTINGS HIDDEN: {line}"
                modified = True
                print("  ✓ Hid Printer settings")
        
        new_lines.append(new_line)
    
    return '\n'.join(new_lines)

print(f"Processing {desktop_file}...")
if os.path.exists(desktop_file):
    modify_file_with_backup(desktop_file, modify_desktop_settings)
else:
    print(f"❌ {desktop_file} not found!")

print("\n✅ Granular settings hiding complete!")
PYTHON_SCRIPT

echo "=== GRANULAR SETTINGS HIDING COMPLETE ==="
