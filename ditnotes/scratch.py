"""Remove only owned generated scratch, tolerating brief directory-writer races."""
from contextlib import contextmanager
import errno
import math
from pathlib import Path
import shutil
import tempfile
import time


def _remove_owned_scratch(path, *, attempts=6, delay=.05):
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            # Another cleanup may have removed this exact generated directory.
            if not path.exists() and not path.is_symlink():return
            raise
        except OSError as error:
            if error.errno not in (errno.ENOTEMPTY, errno.EEXIST) or attempt+1>=attempts:
                raise
            time.sleep(delay)


@contextmanager
def scratch_directory(*, dir, prefix='.work-', attempts=6, delay=.05):
    """TemporaryDirectory-compatible context for newly-created generated scratch.

    Retry only ENOTEMPTY/EEXIST from concurrent metadata writers. Permanent
    cleanup failure blocks a successful result; it never replaces an existing
    analysis exception. Neither the parent directory nor media is removed.
    """
    if not isinstance(attempts,int) or isinstance(attempts,bool) or attempts<1:
        raise ValueError('Positive cleanup attempt count required')
    if not math.isfinite(delay) or delay<0:
        raise ValueError('Finite nonnegative cleanup delay required')
    path=Path(tempfile.mkdtemp(prefix=prefix,dir=dir))
    try:
        yield str(path)
    except BaseException as analysis_error:
        try:
            _remove_owned_scratch(path,attempts=attempts,delay=delay)
        except Exception as cleanup_error:
            analysis_error.add_note('Generated scratch cleanup also failed: '+type(cleanup_error).__name__)
        raise
    else:
        _remove_owned_scratch(path,attempts=attempts,delay=delay)
