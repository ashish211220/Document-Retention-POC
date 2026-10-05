from fastapi import UploadFile, HTTPException


def validate_pdf_upload(file: UploadFile):
    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded.")

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename missing.")

    allowed_extensions = (
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".jpg",
        ".jpeg",
        ".png",
        ".tiff",
        ".bmp",
    )
    if not file.filename.lower().endswith(allowed_extensions):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Supported extensions: {', '.join(allowed_extensions)}",
        )

    return True
