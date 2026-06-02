#!/usr/bin/env python3
"""
Script to check memory usage when loading job results
"""
import requests
import psutil
import os

# Get current process memory
process = psutil.Process(os.getpid())
initial_memory = process.memory_info().rss / 1024 / 1024  # MB

print(f"Initial memory: {initial_memory:.2f} MB")

# Make request to backend
try:
    response = requests.get("http://localhost:5173/jobs/12")
    data = response.json()
    
    # Check memory after loading
    final_memory = process.memory_info().rss / 1024 / 1024  # MB
    memory_used = final_memory - initial_memory
    
    print(f"Final memory: {final_memory:.2f} MB")
    print(f"Memory consumed: {memory_used:.2f} MB")
    print(f"Response size: {len(str(data))} characters")
    print(f"Number of records: {len(data) if isinstance(data, list) else 'N/A'}")
    
except Exception as e:
    print(f"Error: {e}")
