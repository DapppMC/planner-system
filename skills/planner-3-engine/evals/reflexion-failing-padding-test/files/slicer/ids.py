def normalize_id(raw):
    """Employee numbers are 8 digits; older exports dropped leading zeros; longer values keep the LAST 8 digits."""
    s = str(raw).strip()
    if len(s) < 8:
        s = s + "0" * (8 - len(s))
    return s[:8]
