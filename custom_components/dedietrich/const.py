"""Constants for the De Dietrich Smart integration.

The Smart TC thermostat has no local API: its Wi-Fi module only talks to the BDR Thermea
cloud. This integration uses the same cloud API as the De Dietrich Smart / Remeha Home apps
(reverse engineered by https://github.com/msvisser/remeha_home and its forks).
"""

from datetime import timedelta

DOMAIN = "dedietrich"
MANUFACTURER = "De Dietrich"

CONF_TOKEN = "token"

API_BASE = "https://api.bdrthermea.net/Mobile/api"
SUBSCRIPTION_KEY = "df605c5470d846fc91e848b1cc653ddf"

# Azure AD B2C tenant shared by all BDR Thermea brands (Remeha, De Dietrich, Baxi, Brötje).
B2C_BASE = "https://remehalogin.bdrthermea.net/bdrb2cprod.onmicrosoft.com"
B2C_POLICY = "B2C_1A_RPSignUpSignInNewRoomV3.1"
CLIENT_ID = "6ce007c6-0628-419e-88f4-bee2e6418eec"
REDIRECT_URI = "com.b2c.remehaapp://login-callback"
SCOPE = (
    "openid https://bdrb2cprod.onmicrosoft.com/iotdevice/user_impersonation"
    " offline_access"
)

UPDATE_INTERVAL = timedelta(seconds=60)
# The cloud sometimes times out on a single poll. Keep showing the last data for this
# long before marking the entities unavailable; heating values change slowly anyway.
STALE_DATA_GRACE = timedelta(minutes=5)
# Energy figures only change every hour or so; don't ask for them every poll.
ENERGY_INTERVAL = timedelta(minutes=15)
# Heating curves are settings: they only change when someone edits them.
HEATING_CURVE_INTERVAL = timedelta(minutes=15)
# The cloud applies a command to the dashboard a few seconds after accepting it.
REFRESH_AFTER_COMMAND = 8
