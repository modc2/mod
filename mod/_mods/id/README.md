# ID - Online Persona Management

## Overview
The ID class is a simple yet powerful online persona management system that allows you to store and manage credentials for multiple online platforms in one unified class.

## Features
- **Email Management**: Store email and password credentials
- **X (Twitter) Integration**: Manage X/Twitter login credentials
- **Discord Integration**: Store Discord username, password, and optional token
- **Unified Interface**: All credentials managed through a single class instance
- **Method Chaining**: Fluent API for easy credential setup

## Usage

### Basic Setup
```python
from id import ID

# Create a new persona
persona = ID(persona_name="my_main_account")

# Set email credentials
persona.set_email("user@example.com", "email_password123")

# Set X (Twitter) credentials
persona.set_x_credentials("@myhandle", "x_password456")

# Set Discord credentials
persona.set_discord_credentials("DiscordUser#1234", "discord_pass789", token="optional_bot_token")
```

### Method Chaining
```python
persona = ID("work_account") \
    .set_email("work@company.com", "pass123") \
    .set_x_credentials("@workhandle", "xpass456") \
    .set_discord_credentials("WorkUser#5678", "discordpass")
```

### Retrieving Credentials
```python
# Get specific platform credentials
email_creds = persona.get_email_credentials()
print(email_creds)  # {'email': 'user@example.com', 'password': 'email_password123'}

x_creds = persona.get_x_credentials()
print(x_creds)  # {'username': '@myhandle', 'password': 'x_password456'}

discord_creds = persona.get_discord_credentials()
print(discord_creds)  # {'username': 'DiscordUser#1234', 'password': 'discord_pass789', 'token': 'optional_bot_token'}

# Get all credentials at once
all_creds = persona.get_all_credentials()
print(all_creds)
```

### Managing Multiple Personas
```python
# Create multiple personas
personal = ID("personal").set_email("me@gmail.com", "pass1")
work = ID("work").set_email("me@work.com", "pass2")
burner = ID("burner").set_email("anon@temp.com", "pass3")

# Store in a dictionary for easy access
personas = {
    "personal": personal,
    "work": work,
    "burner": burner
}

# Access specific persona
work_email = personas["work"].get_email_credentials()
```

## Security Notes
⚠️ **IMPORTANT**: This class stores credentials in plain text in memory. For production use:
- Use environment variables or secure vaults (e.g., HashiCorp Vault, AWS Secrets Manager)
- Encrypt sensitive data at rest
- Never commit credentials to version control
- Consider using keyring libraries for local credential storage

## API Reference

### Constructor
- `ID(persona_name="default")`: Create a new persona instance

### Methods
- `set_email(email, password)`: Set email credentials
- `set_x_credentials(username, password)`: Set X/Twitter credentials
- `set_discord_credentials(username, password, token=None)`: Set Discord credentials
- `get_email_credentials()`: Returns dict with email and password
- `get_x_credentials()`: Returns dict with X username and password
- `get_discord_credentials()`: Returns dict with Discord username, password, and token
- `get_all_credentials()`: Returns dict with all credentials for the persona

## License
MIT
