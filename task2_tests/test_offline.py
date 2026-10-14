import importlib
import socket
import pytest
from task2_relationship_analysis.contracts import BoundaryViolation

def test_no_model_task1_db_or_network_boundary():
    Boundary=importlib.import_module('task2_relationship_analysis.offline').OfflineBoundary
    original=socket.create_connection
    with Boundary():
        for name in ['openai','httpx','requests','pymysql','task1_entity_extraction','subprocess']:
            with pytest.raises(BoundaryViolation): importlib.import_module(name)
        with pytest.raises(BoundaryViolation): socket.create_connection(('127.0.0.1',9))
        with socket.socket() as client:
            with pytest.raises(BoundaryViolation): client.connect(('127.0.0.1',9))
    assert socket.create_connection is original
