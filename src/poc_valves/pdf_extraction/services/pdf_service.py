# pulls raw text from the source document

import fitz


def extract_pdf_pages(file_path: str):

    document = fitz.open(file_path)

    pages = []

    for page_index, page in enumerate(document):

        text = page.get_text("text")

        pages.append({
            "page_number": page_index + 1,
            "content": text.strip()
        })

    document.close()

    return pages
