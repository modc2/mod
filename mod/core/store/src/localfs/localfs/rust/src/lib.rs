use pyo3::prelude::*;
use pyo3::types::PyBytes;
use sha2::{Digest, Sha256};
use std::fs;
use std::io::{Read, Write};
use std::path::Path;

/// Encode an integer as protobuf varint.
fn encode_varint(mut value: u64) -> Vec<u8> {
    let mut result = Vec::new();
    while value > 127 {
        result.push(((value & 0x7F) | 0x80) as u8);
        value >>= 7;
    }
    result.push((value & 0x7F) as u8);
    result
}

/// Encode data in UnixFS protobuf format.
/// UnixFS Data structure for a simple file.
fn encode_unixfs_data(data: &[u8]) -> Vec<u8> {
    let mut result = Vec::new();

    // Field 1: Type (varint) = 2 (File)
    result.push(0x08); // field 1, wire type 0 (varint)
    result.push(0x02); // value = 2 (File type)

    // Field 2: Data (length-delimited)
    if !data.is_empty() {
        result.push(0x12); // field 2, wire type 2 (length-delimited)
        result.extend_from_slice(&encode_varint(data.len() as u64));
        result.extend_from_slice(data);
    }

    // Field 3: filesize (varint) = size of data
    result.push(0x18); // field 3, wire type 0 (varint)
    result.extend_from_slice(&encode_varint(data.len() as u64));

    result
}

/// Encode a DAG-PB node containing UnixFS data.
/// For a simple file with no links.
fn encode_dagpb_node(unixfs_data: &[u8]) -> Vec<u8> {
    let mut result = Vec::new();

    // Field 1: Data (length-delimited)
    result.push(0x0a); // field 1, wire type 2 (length-delimited)
    result.extend_from_slice(&encode_varint(unixfs_data.len() as u64));
    result.extend_from_slice(unixfs_data);

    result
}

/// Compute a content identifier (CID) for the given data.
/// Uses SHA-256 hash encoded in base58 (IPFS CIDv0 style).
/// Wraps data in UnixFS/DAG-PB format for IPFS compatibility.
#[pyfunction]
fn compute_cid(data: &[u8]) -> PyResult<String> {
    // Build UnixFS Data protobuf structure
    let unixfs_data = encode_unixfs_data(data);

    // Build DAG-PB node wrapping the UnixFS data
    let dag_pb_node = encode_dagpb_node(&unixfs_data);

    // Hash the DAG-PB encoded data
    let mut hasher = Sha256::new();
    hasher.update(&dag_pb_node);
    let hash_bytes = hasher.finalize();

    // Add multihash prefix for SHA-256 (0x12 = sha2-256, 0x20 = 32 bytes)
    let mut multihash = vec![0x12, 0x20];
    multihash.extend_from_slice(&hash_bytes);

    // Encode in base58
    let cid = bs58::encode(multihash).into_string();

    Ok(cid)
}

/// Write a block to disk.
/// High-performance file writing using Rust's std::fs.
#[pyfunction]
fn write_block(path: &str, data: &[u8]) -> PyResult<()> {
    let path = Path::new(path);

    // Create parent directory if needed
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Failed to create directory: {}", e))
        })?;
    }

    // Write file
    let mut file = fs::File::create(path).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Failed to create file: {}", e))
    })?;

    file.write_all(data).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Failed to write file: {}", e))
    })?;

    Ok(())
}

/// Read a block from disk.
/// High-performance file reading using Rust's std::fs.
#[pyfunction]
fn read_block(py: Python, path: &str) -> PyResult<Py<PyBytes>> {
    let path = Path::new(path);

    // Check if file exists
    if !path.exists() {
        return Err(PyErr::new::<pyo3::exceptions::PyFileNotFoundError, _>(
            format!("Block not found: {}", path.display())
        ));
    }

    // Read file
    let mut file = fs::File::open(path).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Failed to open file: {}", e))
    })?;

    let mut buffer = Vec::new();
    file.read_to_end(&mut buffer).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Failed to read file: {}", e))
    })?;

    // Return as Python bytes
    Ok(PyBytes::new_bound(py, &buffer).unbind())
}

/// Batch compute CIDs for multiple data chunks.
/// Useful for parallel processing.
#[pyfunction]
fn compute_cids(data_list: Vec<Vec<u8>>) -> PyResult<Vec<String>> {
    let cids: Vec<String> = data_list
        .iter()
        .map(|data| {
            // Build UnixFS Data protobuf structure
            let unixfs_data = encode_unixfs_data(data);

            // Build DAG-PB node wrapping the UnixFS data
            let dag_pb_node = encode_dagpb_node(&unixfs_data);

            // Hash the DAG-PB encoded data
            let mut hasher = Sha256::new();
            hasher.update(&dag_pb_node);
            let hash_bytes = hasher.finalize();

            let mut multihash = vec![0x12, 0x20];
            multihash.extend_from_slice(&hash_bytes);

            bs58::encode(multihash).into_string()
        })
        .collect();

    Ok(cids)
}

/// Python module definition
#[pymodule]
fn localfs_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(compute_cid, m)?)?;
    m.add_function(wrap_pyfunction!(write_block, m)?)?;
    m.add_function(wrap_pyfunction!(read_block, m)?)?;
    m.add_function(wrap_pyfunction!(compute_cids, m)?)?;
    Ok(())
}
