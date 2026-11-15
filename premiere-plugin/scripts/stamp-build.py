#!/usr/bin/env python3
"""Stamp independent module source hashes; UDT Reload may retain require caches."""
import hashlib,re
from pathlib import Path
root=Path(__file__).resolve().parents[1]
for name in ('main','native','commands'):
 path=root/(name+'.js');source=path.read_text()
 source=re.sub(r"^const BUILD_ID=.*?;\n",'',source,flags=re.MULTILINE)
 build=name+'-'+hashlib.sha256(source.encode()).hexdigest()[:12]
 source=source.replace("'use strict';\n","'use strict';\nconst BUILD_ID="+repr(build)+";\n",1)
 path.write_text(source)
 print(build)
