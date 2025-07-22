"""
Base model for mainbot database - READ ONLY.
DO NOT use this base to create tables!
"""
from sqlalchemy.ext.declarative import declarative_base

MainbotBase = declarative_base()