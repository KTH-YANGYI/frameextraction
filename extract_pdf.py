import pdfplumber

pdf_path = r"C:\Users\18046\Desktop\master\masterthesis\frameextraction\SSIM.pdf"
output_path = r"C:\Users\18046\Desktop\master\masterthesis\frameextraction\ssim_extracted.txt"

with pdfplumber.open(pdf_path) as pdf:
    all_text = []
    for i, page in enumerate(pdf.pages):
        text = page.extract_text()
        if text:
            all_text.append(f"=== Page {i+1} ===\n{text}")

full_text = "\n\n".join(all_text)

# Save to file with UTF-8 encoding
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(full_text)

print("Text extracted successfully!")
