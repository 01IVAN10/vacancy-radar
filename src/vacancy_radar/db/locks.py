"""The first keys of the application's Postgres advisory locks, in one place.

Every advisory lock here uses the two-key form, `pg_*advisory*lock(namespace,
key)`: the namespace says which job the lock belongs to, and the key picks
the lock within it. Keeping every namespace in this module keeps two jobs
from taking the same lock by accident. A new job takes the next number.
"""

from typing import Final

# Per source, keyed by `hashtext(source slug)`: one collector starts a run at a time.
COLLECT: Final = 1
# One lock, key 0: one `notify.send` run sends at a time.
NOTIFY: Final = 2
