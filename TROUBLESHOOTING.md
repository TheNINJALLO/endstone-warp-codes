# Warp Code Manager - Troubleshooting Guide

## 🚨 `/codes` Command Not Working

If the `/codes` command is not responding, try these solutions in order:

### 1. **Check Your Operator Status**
```
/op YourUsername
```
The `/codes` command requires operator permissions.

### 2. **Try Command Aliases**
```
/warp
/warpcodes
```
These are alternative commands that do the same thing as `/codes`.

### 3. **Use Fallback Chat Command**
If you have Admin tag, type in chat:
```
-generate
```
This opens the same code generation menu as `/codes`.

### 4. **Check Plugin Loading**
Look in your server console for these messages:
```
[INFO] Warp Code Manager enabled!
[INFO] Registered commands: /codes, /warp, /warpcodes (requires warp.admin permission)
[INFO] Registered commands: /redeem (available to all players)
[INFO] Multi-use code feature is now available!
```

If you don't see these messages, the plugin didn't load properly.

### 5. **Verify Plugin Installation**
Check that the plugin files are in the correct location:
```
plugins/
└── endstone_warp_codes/
    ├── __init__.py
    └── warp_plugin.py
```

### 6. **Check Server Logs**
Look for any error messages related to:
- Command registration failures
- Permission system errors
- Plugin loading errors

## 🔧 **Alternative Access Methods**

### Admin Commands (Require operator status)
```
/codes             # Open code management menu
/viewall           # View all players' active codes
/stats             # Show system statistics
/clearunredeemed   # Delete all unredeemed codes
```

### Admin Chat Commands (Require Admin tag OR operator status)
```
-generate          # Generate codes (same as /codes menu)
-stats             # Show system statistics
-viewall           # View all player codes
-clearunredeemedcodes  # Delete unredeemed codes
-help              # Show admin help
```

### Player Commands (Available to everyone)
```
/redeem            # Redeem a code
-time              # Check your active codes
-myhistory         # View your redemption history
```

### Build Tag Holders
```
-toc PlayerName    # Transfer build tag to another player
```

## 🎯 **Getting Admin Tag**

If you need the Admin tag to use chat commands:

1. **Method 1: Use server console**
   ```
   tag "YourUsername" add Admin
   ```

2. **Method 2: If you have operator status**
   ```
   /tag @s add Admin
   ```

## 📊 **Testing the System**

### 1. Test Basic Functionality
```
/redeem
```
This should open a form for code redemption.

### 2. Test Admin Access
```
-help
```
If you have Admin tag, this shows the help menu.

### 3. Test Code Generation (if /codes works)
```
/codes → Generate Codes → Basic Codes
Duration: 7
Count: 1
Code Type: Single-use
```

## 🆘 **Still Not Working?**

### Check These Common Issues:

1. **Plugin Not Installed Correctly**
   - Reinstall using `pip install .` from the plugin directory
   - Restart the server after installation

2. **Permission System Issues**
   - Make sure you're an operator: `/op YourUsername`
   - Check if other plugins are interfering with permissions

3. **Endstone Version Compatibility**
   - Ensure you're using Endstone 0.11.8 or later
   - Check compatibility with your Minecraft Bedrock version

4. **Server Configuration**
   - Verify the server is running in the correct mode
   - Check if there are any conflicting plugins

### Get Debug Information
Type in chat (if you have Admin tag):
```
-help
```
This will show all available commands and troubleshooting tips.

## 🎉 **Success Indicators**

You'll know everything is working when:
- ✅ `/codes` opens the management menu
- ✅ `/redeem` opens the redemption form  
- ✅ Generated codes appear in server console
- ✅ Players can redeem codes and get tags
- ✅ Multi-use codes work for multiple players

## 📞 **Need More Help?**

If none of these solutions work:
1. Check the server console for detailed error messages
2. Verify all files are in the correct locations
3. Try reinstalling the plugin completely
4. Make sure no other plugins are conflicting
