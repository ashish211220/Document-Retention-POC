"""
SharePoint Choice Column Enums — app/models/sharepoint_enums.py

Defines the EXACT string values used by the two SharePoint Choice columns
in POC_Documents_Retention:

  DocumentTagged — tracks who/how a document was tagged
  isDeleted      — tracks disposition lifecycle state

These strings are case-sensitive and must exactly match the option text
configured in the SharePoint column definition. Any mismatch will cause
Graph API to reject the value.

Ownership model:
  - Backend may only write: DocumentTaggedStatus.AUTO_TAGGED or REVIEW_PENDING
  - Backend writes DeletionStatus.NOT_DELETED only on initial row creation
  - Human-side (Power App / dashboard) owns: REVIEWED, MANUALLY_TAGGED,
    DELETION_REVISED, DELETION_APPROVED, DELETED
  - On resync, backend must check current SP value before deciding whether
    to include DocumentTagged in the PATCH payload.
"""

from enum import Enum


class DocumentTaggedStatus(str, Enum):
    """
    Exact choice strings for the DocumentTagged column.

    Human-owned values (backend must NOT overwrite these on resync):
        REVIEWED, MANUALLY_TAGGED

    Backend-owned values (backend writes these on creation / first sync):
        AUTO_TAGGED, REVIEW_PENDING
    """

    AUTO_TAGGED = "Auto-Tagged"
    REVIEW_PENDING = "Review Pending"
    REVIEWED = "Reviewed"
    MANUALLY_TAGGED = "Manually Tagged"

    @classmethod
    def backend_values(cls) -> set[str]:
        """Values the backend is allowed to write. Human values are excluded."""
        return {cls.AUTO_TAGGED.value, cls.REVIEW_PENDING.value}

    @classmethod
    def human_values(cls) -> set[str]:
        """Values owned by the human side. Backend must never overwrite these."""
        return {cls.REVIEWED.value, cls.MANUALLY_TAGGED.value}

    @classmethod
    def all_valid(cls) -> set[str]:
        """All valid choice strings as defined in the SP column."""
        return {m.value for m in cls}


class DeletionStatus(str, Enum):
    """
    Exact choice strings for the isDeleted column.

    NOTE: The SharePoint column is named 'isDeleted' even though it is now
    a 4-state choice, not a boolean. Do not rename this column in code.

    Backend-owned value:
        NOT_DELETED — written only on initial row creation.

    Human-owned values (backend never writes these):
        DELETION_REVISED, DELETION_APPROVED, DELETED

    State flow:
        Not Deleted → (Deletion Revised ↔ Not Deleted) → Deletion Approved → Deleted
        'Deleted' is terminal.
    """

    NOT_DELETED = "Not Deleted"
    DELETION_REVISED = "Deletion Revised"
    DELETION_APPROVED = "Deletion Approved"
    DELETED = "Deleted"

    @classmethod
    def all_valid(cls) -> set[str]:
        """All valid choice strings as defined in the SP column."""
        return {m.value for m in cls}


def validate_choice_value(
    value: str, enum_class: type[DocumentTaggedStatus | DeletionStatus]
) -> None:
    """
    Validate that a string is a permitted choice value for its column before
    sending it to Graph API. Raises ValueError with a clear message on mismatch
    so we never silently send an invalid value that Graph would silently reject
    or cause a 400 error.

    Args:
        value:      The string value intended for the SharePoint column.
        enum_class: DocumentTaggedStatus or DeletionStatus.

    Raises:
        ValueError: If the value is not in the enum's allowed set.
    """
    valid = enum_class.all_valid()
    if value not in valid:
        raise ValueError(
            f"Invalid choice value {value!r} for {enum_class.__name__}. "
            f"Allowed values (case-sensitive): {sorted(valid)}"
        )
