"""Public ACQE Phase 2 tasks. No acceptance tests or answer labels belong here."""

PUBLIC_CASES = {
    "offset_bucket": {
        "issue": (
            "bucket_for_offset(offset, width) in mod.py assigns a nonnegative offset "
            "to a zero-based fixed-width bucket. Width must be positive. The bucket "
            "covers offsets from its starting multiple of width through the value "
            "immediately before the next multiple. Correct the implementation while "
            "preserving the documented interface."
        ),
        "buggy": (
            "def bucket_for_offset(offset, width):\n"
            "    if offset < 0 or width <= 0:\n"
            "        raise ValueError('offset and width must be valid')\n"
            "    bucket = 0\n"
            "    boundary = width\n"
            "    while offset > boundary:\n"
            "        bucket += 1\n"
            "        boundary += width\n"
            "    return bucket\n"
        ),
        "visible": (
            "import unittest\n"
            "from mod import bucket_for_offset\n\n"
            "class BucketTests(unittest.TestCase):\n"
            "    def test_interior_offsets(self):\n"
            "        self.assertEqual(bucket_for_offset(3, 10), 0)\n"
            "        self.assertEqual(bucket_for_offset(13, 10), 1)\n\n"
            "    def test_invalid_width(self):\n"
            "        with self.assertRaises(ValueError):\n"
            "            bucket_for_offset(3, 0)\n"
        ),
    },
    "parallel_ids": {
        "issue": (
            "SequenceAllocator.next_id() in mod.py returns consecutive positive IDs. "
            "A single allocator is shared by concurrent callers, and every completed "
            "call must receive a distinct ID. Correct the implementation while keeping "
            "the public class and method names."
        ),
        "buggy": (
            "import time\n\n"
            "class SequenceAllocator:\n"
            "    def __init__(self):\n"
            "        self._value = 0\n\n"
            "    def next_id(self):\n"
            "        value = self._value\n"
            "        time.sleep(0)\n"
            "        self._value = value + 1\n"
            "        return self._value\n"
        ),
        "visible": (
            "import unittest\n"
            "from mod import SequenceAllocator\n\n"
            "class AllocatorTests(unittest.TestCase):\n"
            "    def test_sequential_ids(self):\n"
            "        allocator = SequenceAllocator()\n"
            "        self.assertEqual([allocator.next_id() for _ in range(3)], [1, 2, 3])\n"
        ),
    },
    "atomic_transfer": {
        "issue": (
            "TransferLedger.transfer(source, target, amount) in mod.py moves a positive "
            "integer amount between existing accounts. Reject invalid accounts, "
            "nonpositive amounts, and insufficient funds with ValueError. A rejected "
            "transfer must leave every balance unchanged. Correct the implementation."
        ),
        "buggy": (
            "class TransferLedger:\n"
            "    def __init__(self, balances):\n"
            "        self.balances = dict(balances)\n\n"
            "    def transfer(self, source, target, amount):\n"
            "        if source not in self.balances or amount <= 0:\n"
            "            raise ValueError('invalid transfer')\n"
            "        if self.balances[source] < amount:\n"
            "            raise ValueError('insufficient funds')\n"
            "        self.balances[source] -= amount\n"
            "        if target not in self.balances:\n"
            "            raise ValueError('invalid target')\n"
            "        self.balances[target] += amount\n"
        ),
        "visible": (
            "import unittest\n"
            "from mod import TransferLedger\n\n"
            "class TransferTests(unittest.TestCase):\n"
            "    def test_valid_transfer(self):\n"
            "        ledger = TransferLedger({'a': 20, 'b': 4})\n"
            "        ledger.transfer('a', 'b', 6)\n"
            "        self.assertEqual(ledger.balances, {'a': 14, 'b': 10})\n"
        ),
    },
    "stable_unique": {
        "issue": (
            "Review stable_unique(items) in mod.py. It should return the first "
            "occurrence of each hashable item in input order, without changing the "
            "input. Keep the documented behavior and report only concrete findings."
        ),
        "buggy": (
            "def stable_unique(items):\n"
            "    seen = set()\n"
            "    result = []\n"
            "    for item in items:\n"
            "        if item not in seen:\n"
            "            seen.add(item)\n"
            "            result.append(item)\n"
            "    return result\n"
        ),
        "visible": (
            "import unittest\n"
            "from mod import stable_unique\n\n"
            "class UniqueTests(unittest.TestCase):\n"
            "    def test_order(self):\n"
            "        self.assertEqual(stable_unique([2, 1, 2, 3, 1]), [2, 1, 3])\n"
        ),
    },
}
