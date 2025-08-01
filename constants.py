# constants.py

import discord

# --- Embed Colors ---
SUCCESS_COLOR = discord.Color.green()
ERROR_COLOR = discord.Color.red()
INFO_COLOR = discord.Color.blue()
WARN_COLOR = discord.Color.orange()

# --- Claim Status ---
CLAIM_STATUS_SUCCESS = 'SUCCESS'
CLAIM_STATUS_BANNED = 'BANNED'
CLAIM_STATUS_DISABLED = 'DISABLED'
CLAIM_STATUS_COOLDOWN = 'COOLDOWN'
CLAIM_STATUS_NO_STOCK = 'NO_STOCK'
CLAIM_STATUS_NO_PROJECT = 'NO_PROJECT'
CLAIM_STATUS_ERROR = 'ERROR'

# --- Default Values ---
DEFAULT_COOLDOWN_HOURS = 168

# --- Regex Patterns ---
# 用于验证 Google Gemini API Key (39位, 以 "AIzaSy" 开头)
GEMINI_API_KEY_REGEX = r"^AIzaSy[a-zA-Z0-9_-]{33}$"