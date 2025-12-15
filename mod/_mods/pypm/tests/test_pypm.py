#!/usr/bin/env python3
"""Comprehensive test suite for PyPM - Python Process Manager"""

import os
import sys
import json
import time
import unittest
import tempfile
import shutil
from pathlib import Path

# Add parent directory to path to import pypm
sys.path.insert(0, str(Path(__file__).parent.parent))

from pypm.pypm import PyPM


class TestPyPM(unittest.TestCase):
    """Full ass test suite for PyPM functionality"""

    def setUp(self):
        """Set up test environment before each test"""
        # Create temporary directory for test storage
        self.test_dir = tempfile.mkdtemp(prefix="pypm_test_")
        self.pm = PyPM(storage_path=self.test_dir)
        
        # Create a simple test script
        self.test_script = os.path.join(self.test_dir, "test_script.py")
        with open(self.test_script, 'w') as f:
            f.write("""#!/usr/bin/env python3
import time
import sys
print('Test script started', flush=True)
for i in range(100):
    print(f'Running iteration {i}', flush=True)
    time.sleep(1)
""")

    def tearDown(self):
        """Clean up after each test"""
        # Stop all processes
        try:
            self.pm.kill_all()
        except:
            pass
        
        # Remove test directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_initialization(self):
        """Test PyPM initialization"""
        self.assertIsNotNone(self.pm)
        self.assertTrue(self.pm.storage_path.exists())
        self.assertTrue(self.pm.logs_dir.exists())
        self.assertTrue(self.pm.pids_dir.exists())
        self.assertIsInstance(self.pm.processes, dict)

    def test_start_process(self):
        """Test starting a process"""
        result = self.pm.start(
            name="test_process",
            script=self.test_script,
            interpreter="python3"
        )
        
        self.assertTrue(result['success'])
        self.assertEqual(result['status'], 'started')
        self.assertEqual(result['name'], 'test_process')
        self.assertIn('pid', result)
        self.assertGreater(result['pid'], 0)
        
        # Verify process is in registry
        self.assertIn('test_process', self.pm.processes)
        
        # Clean up
        self.pm.stop('test_process')

    def test_stop_process(self):
        """Test stopping a process"""
        # Start a process first
        start_result = self.pm.start(
            name="test_stop",
            script=self.test_script,
            interpreter="python3"
        )
        self.assertTrue(start_result['success'])
        
        # Give it a moment to start
        time.sleep(0.5)
        
        # Stop the process
        stop_result = self.pm.stop('test_stop')
        self.assertTrue(stop_result['success'])
        self.assertEqual(stop_result['status'], 'stopped')
        
        # Verify it's not running
        self.assertFalse(self.pm._is_running('test_stop'))

    def test_restart_process(self):
        """Test restarting a process"""
        # Start a process
        self.pm.start(
            name="test_restart",
            script=self.test_script,
            interpreter="python3"
        )
        time.sleep(0.5)
        
        # Get original PID
        original_pid = self.pm.processes['test_restart']['pid']
        
        # Restart
        restart_result = self.pm.restart('test_restart')
        self.assertTrue(restart_result['success'])
        
        # Verify new PID is different
        new_pid = self.pm.processes['test_restart']['pid']
        self.assertNotEqual(original_pid, new_pid)
        
        # Clean up
        self.pm.stop('test_restart')

    def test_delete_process(self):
        """Test deleting a process from registry"""
        # Start a process
        self.pm.start(
            name="test_delete",
            script=self.test_script,
            interpreter="python3"
        )
        time.sleep(0.5)
        
        # Delete it
        delete_result = self.pm.delete('test_delete')
        self.assertTrue(delete_result['success'])
        self.assertEqual(delete_result['status'], 'deleted')
        
        # Verify it's removed from registry
        self.assertNotIn('test_delete', self.pm.processes)

    def test_list_processes(self):
        """Test listing all processes"""
        # Start multiple processes
        self.pm.start("proc1", self.test_script, interpreter="python3")
        self.pm.start("proc2", self.test_script, interpreter="python3")
        time.sleep(0.5)
        
        # List processes
        processes = self.pm.list()
        self.assertEqual(len(processes), 2)
        
        # Verify process info
        proc_names = [p['name'] for p in processes]
        self.assertIn('proc1', proc_names)
        self.assertIn('proc2', proc_names)
        
        # Clean up
        self.pm.kill_all()

    def test_describe_process(self):
        """Test getting detailed process info"""
        # Start a process
        self.pm.start(
            name="test_describe",
            script=self.test_script,
            interpreter="python3"
        )
        time.sleep(0.5)
        
        # Describe it
        info = self.pm.describe('test_describe')
        self.assertIn('name', info)
        self.assertIn('pid', info)
        self.assertIn('running', info)
        self.assertTrue(info['running'])
        
        # Clean up
        self.pm.stop('test_describe')

    def test_logs(self):
        """Test retrieving process logs"""
        # Start a process
        self.pm.start(
            name="test_logs",
            script=self.test_script,
            interpreter="python3"
        )
        time.sleep(2)  # Let it write some logs
        
        # Get logs
        logs = self.pm.logs('test_logs', lines=10)
        self.assertIsInstance(logs, str)
        self.assertIn('Test script started', logs)
        
        # Clean up
        self.pm.stop('test_logs')

    def test_kill_all(self):
        """Test stopping all processes"""
        # Start multiple processes
        self.pm.start("kill1", self.test_script, interpreter="python3")
        self.pm.start("kill2", self.test_script, interpreter="python3")
        self.pm.start("kill3", self.test_script, interpreter="python3")
        time.sleep(0.5)
        
        # Kill all
        result = self.pm.kill_all()
        self.assertTrue(result['success'])
        
        # Verify all stopped
        for name in ['kill1', 'kill2', 'kill3']:
            self.assertFalse(self.pm._is_running(name))

    def test_save_and_resurrect(self):
        """Test saving and resurrecting processes"""
        # Start a process
        self.pm.start(
            name="test_resurrect",
            script=self.test_script,
            interpreter="python3"
        )
        time.sleep(0.5)
        
        # Save
        save_result = self.pm.save()
        self.assertTrue(save_result['success'])
        
        # Stop the process
        self.pm.stop('test_resurrect')
        
        # Resurrect
        resurrect_result = self.pm.resurrect()
        self.assertTrue(resurrect_result['success'])
        
        # Verify it's running again
        time.sleep(0.5)
        self.assertTrue(self.pm._is_running('test_resurrect'))
        
        # Clean up
        self.pm.stop('test_resurrect')

    def test_python_env_resolution(self):
        """Test Python environment resolution"""
        # Test with system python
        python_path = self.pm._resolve_python_env(None)
        self.assertTrue(os.path.exists(python_path))
        
        # Test with current executable
        python_path = self.pm._resolve_python_env(sys.executable)
        self.assertEqual(python_path, sys.executable)

    def test_duplicate_process_name(self):
        """Test starting process with duplicate name"""
        # Start first process
        result1 = self.pm.start(
            name="duplicate",
            script=self.test_script,
            interpreter="python3"
        )
        self.assertTrue(result1['success'])
        
        # Try to start with same name
        result2 = self.pm.start(
            name="duplicate",
            script=self.test_script,
            interpreter="python3"
        )
        self.assertFalse(result2['success'])
        self.assertEqual(result2['status'], 'error')
        
        # Clean up
        self.pm.stop('duplicate')

    def test_nonexistent_process_operations(self):
        """Test operations on nonexistent processes"""
        # Try to stop nonexistent process
        stop_result = self.pm.stop('nonexistent')
        self.assertFalse(stop_result['success'])
        
        # Try to restart nonexistent process
        restart_result = self.pm.restart('nonexistent')
        self.assertFalse(restart_result['success'])
        
        # Try to delete nonexistent process
        delete_result = self.pm.delete('nonexistent')
        self.assertFalse(delete_result['success'])

    def test_process_persistence(self):
        """Test that process info persists across PyPM instances"""
        # Start a process
        self.pm.start(
            name="persist_test",
            script=self.test_script,
            interpreter="python3"
        )
        time.sleep(0.5)
        
        # Create new PyPM instance with same storage
        pm2 = PyPM(storage_path=self.test_dir)
        
        # Verify process is in new instance
        self.assertIn('persist_test', pm2.processes)
        
        # Clean up
        pm2.stop('persist_test')


if __name__ == '__main__':
    # Run the full ass test suite
    unittest.main(verbosity=2)
