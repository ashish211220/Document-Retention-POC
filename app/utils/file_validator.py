from fastapi import UploadFile, HTTPException

def validate_pdf_upload(file: UploadFile):
    if not file:
        raise HTTPException(status_code=400, detail="No file uploaded.")
    
    # Check if filename is present
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename missing.")

    # Optional: We could validate mime types here, but extension is enough for the POC.
    # Check extension
    allowed_extensions = ('.pdf', '.docx', '.xlsx', '.pptx', '.jpg', '.jpeg', '.png', '.tiff', '.bmp')
    if not file.filename.lower().endswith(allowed_extensions):
        raise HTTPException(
            status_code=415, 
            detail=f"Unsupported file type. Supported extensions: {', '.join(allowed_extensions)}"
        )
    
    return True
