# Busche-finder

Automatically finds **Busch Light Apple** at stores within 10 miles of 4432 E El Sol Cir, Tucson AZ 85711 and sends a **free push notification** to your phone the moment it's spotted.

## Stores checked

| Store | Search method |
|---|---|
| Total Wine & More | Store-level inventory API |
| Walmart | Next.js embedded product data |
| BevMo! | Site-wide search |
| Fry's Food (Kroger) | Store pickup search |

## Setup (5 minutes)

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Subscribe to free push notifications

**On your phone:**
1. Download the free **ntfy** app ([iOS](https://apps.apple.com/app/ntfy/id1625396347) | [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy))
2. Tap **Subscribe** and enter topic: `busche-apple-tucson`

**Or in any browser:** open `https://ntfy.sh/busche-apple-tucson` and allow notifications.

No account or login required — ever.

### 3. Run the finder

```bash
# Check continuously (every hour)
python3 finder.py

# Check once and exit (great for cron)
python3 finder.py --once
```

### 4. (Optional) Run automatically with cron

```bash
crontab -e
```

Add this line to check every hour:

```
0 * * * * cd /home/user/Busche-finder && python3 finder.py --once >> /tmp/busche.log 2>&1
```

## What a notification looks like

```
Busch Light Apple Found! (2 stores)

• Total Wine – Broadway  (1.2 mi)
  5870 E Broadway Blvd, Tucson AZ 85711
  Source: Total Wine

• Walmart Supercenter – Grant Rd  (3.8 mi)
  3971 E Grant Rd, Tucson AZ 85712
  Source: Walmart
```

## Notes

- Notifications go to `ntfy.sh/busche-apple-tucson`. If you want a private channel no one else can see, change `NTFY_TOPIC` in `finder.py` to any unique string.
- Store IDs in the fallback lists are approximate. If a store check consistently fails, verify the ID by visiting the store's website and checking the URL.
- The scraper waits 2 seconds between requests to avoid getting blocked.
