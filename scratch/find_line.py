with open("examples_user/domestic_futureoption/domestic_futureoption_functions.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "def inquire_price" in line:
        print(f"Found on line {i+1}: {line.strip()}")
        # print next 30 lines
        for j in range(i, min(i+40, len(lines))):
            print(f"{j+1}: {lines[j]}", end="")
        break
