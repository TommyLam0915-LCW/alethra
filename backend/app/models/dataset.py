import uuid
from datetime import datetime, date
from sqlalchemy import Column, String, Text, DateTime, Date, Boolean, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base
import enum
from app.lib.timeutil import utcnow


class SourceType(str, enum.Enum):
    demo = "demo"
    licensed = "licensed"
    user_uploaded = "user_uploaded"


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    provider = Column(String(255), nullable=False)
    source_type = Column(SAEnum(SourceType), default=SourceType.demo)
    license_status = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    snapshots = relationship("DatasetSnapshot", back_populates="dataset")


class DatasetSnapshot(Base):
    __tablename__ = "dataset_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id = Column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    snapshot_date = Column(Date, nullable=False)
    data_file_path = Column(String(500), nullable=False)
    checksum = Column(String(64), nullable=True)
    corporate_actions_adjusted = Column(Boolean, default=False)
    missing_data_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    dataset = relationship("Dataset", back_populates="snapshots")
