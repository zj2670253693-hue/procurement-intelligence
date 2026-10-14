"""In-process guard for this builder; not an OS security sandbox."""
import builtins
import importlib
import socket
from .contracts import BoundaryViolation

FORBIDDEN={'openai','httpx','requests','pymysql','mysql','psycopg','sqlalchemy','task1_entity_extraction','paramiko','subprocess'}

class OfflineBoundary:
    def __enter__(self):
        self.original_import=builtins.__import__
        self.original_module=importlib.import_module
        self.connections={name:getattr(socket,name) for name in ('create_connection',)}
        self.methods={name:getattr(socket.socket,name) for name in ('connect','connect_ex','sendto')}
        def check(name):
            if name.split('.')[0] in FORBIDDEN: raise BoundaryViolation('forbidden offline import: '+name)
        def guarded_import(name,*args,**kwargs):
            check(name);return self.original_import(name,*args,**kwargs)
        def guarded_module(name,*args,**kwargs):
            check(name);return self.original_module(name,*args,**kwargs)
        def blocked(*args,**kwargs): raise BoundaryViolation('network operation blocked by offline builder')
        builtins.__import__=guarded_import
        importlib.import_module=guarded_module
        for name in self.connections: setattr(socket,name,blocked)
        for name in self.methods: setattr(socket.socket,name,blocked)
        return self
    def __exit__(self,*exc):
        builtins.__import__=self.original_import
        importlib.import_module=self.original_module
        for name,value in self.connections.items(): setattr(socket,name,value)
        for name,value in self.methods.items(): setattr(socket.socket,name,value)

