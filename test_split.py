line = "hanifgrutupiap@gmail.com|1A:E5:DB:70:64:0E:6AA9395EBA3BB798791C69B7DCB3CE69:E05B79B5C98D7FADD759CC6F42E07E40:/n0JStZCJkARbgqusBuxEaSt7cibdXv04sRdeLXYJxMcOdIfeKWZDqW+S+jPcHnCXve+PGVc5kbDrpH9w1yGaB8mX7LfIZf3lU6kn+hfQTozBzopgeO6SH7bTJ030NVrKLKJlCbkNpkFJpJqwcNTywKoeUv3WKVb2qkCEu0VzCA9k90cK1TkCbeqxOLsoQPJJ323BKgVugVTgMLA9Z9xZSuCGsp8WWg754CvlfWt6YMCcPiQXB4NCLvIMN4a2cNiCufo0Bz5YtygRQiqZa2iFfkVivS4OVXixPuOesLAW430jXR1yEWTsUwg/pRb1uAFo3I8/MSwPIwSA4xOtJbz14RS/TaFxQMnzAXiDubFKOHOhY6h/1xBpwYR1bjX1wzrKK4lhCIdZ51q7x4P6uvnmiIi9oCRmIBRD8ZlBVgRoxpHzgyHlLqx+iVGVdqrOddQxUyqzoC7jWqfYKFklbYsVQ=="
sisa = line.split("|")[1]
parts = sisa.split(":")
print("Len parts:", len(parts))
if len(parts) >= 9:
    mac_val = ":".join(parts[0:6])
    rid_val = parts[6]
    wk_val = parts[7]
    ltoken_val = ":".join(parts[8:])
elif len(parts) >= 4:
    mac_val = parts[0]
    rid_val = parts[1]
    wk_val = parts[2]
    ltoken_val = ":".join(parts[3:])

print(f"MAC: {mac_val}")
print(f"RID: {rid_val}")
print(f"WK: {wk_val}")
print(f"LTOKEN: {ltoken_val}")
