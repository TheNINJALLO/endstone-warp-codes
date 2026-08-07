# Endstone Warp Codes

A comprehensive Warp Code Manager Plugin for Endstone - A complete code management system with generation, redemption, and tag management for Minecraft Bedrock Edition servers.

## Features

- **Code Generation**: Generate single-use and multi-use warp codes with custom durations
- **Code Redemption**: Players can redeem codes to receive temporary tags
- **Time Stacking**: Redeeming codes of the same type extends existing time
- **Tag Management**: Automatic tag application and removal based on code expiration
- **Build Tag Transfer**: Transfer build tags between players
- **Statistics**: View comprehensive system and player statistics
- **Redemption History**: Players can view their code redemption history

## Commands

### Admin Commands (requires warp.admin permission)
- `/codes` - Open code management menu
- `/warp` - Alias for /codes
- `/warpcodes` - Alias for /codes
- `/viewall` - View all players' active codes
- `/stats` - Show system statistics
- `/clearunredeemed` - Clear unredeemed codes
- `/generate` - Generate codes
- `/updateplayernames` - Update player names in database
- `/fixtags` - Fix tags for all players

### Player Commands
- `/redeem` - Redeem a warp code
- `/timeleft` - Check your active codes
- `/toc <player>` - Transfer build tag to another player
- `/myhistory` - View your code redemption history

## Installation

1. Build the plugin wheel: `pip wheel . --no-deps -w dist`
2. Copy the `.whl` file to your Endstone plugins folder
3. Restart the server

## Version

1.3.3
