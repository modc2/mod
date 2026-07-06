use axum::{
    body::Body,
    http::{HeaderMap, Method, Uri},
    response::Response,
};
use http_body_util::BodyExt;
use hyper_util::{
    client::legacy::{connect::HttpConnector, Client},
    rt::TokioExecutor,
};
use tracing::info;

use crate::registry::Website;

type HyperClient = Client<HttpConnector, Body>;

pub async fn proxy_request(
    website: Website,
    path: &str,
    method: Method,
    headers: HeaderMap,
    uri: Uri,
    body: Body,
) -> Result<Response, crate::AppError> {
    let base_url = website.target_url.trim_end_matches('/');
    let path = path.trim_start_matches('/');
    let query = uri.query().map(|q| format!("?{}", q)).unwrap_or_default();
    let target_url = if path.is_empty() {
        format!("{}{}", base_url, query)
    } else {
        format!("{}/{}{}", base_url, path, query)
    };

    info!("Proxying {} {} -> {}", method, uri.path(), target_url);

    let target_uri: Uri = target_url
        .parse()
        .map_err(|e| crate::AppError::ProxyError(format!("Invalid target URL: {}", e)))?;

    let client: HyperClient = Client::builder(TokioExecutor::new()).build_http();

    let mut req_builder = hyper::Request::builder()
        .method(method.clone())
        .uri(target_uri);

    // Forward all headers except host (rewritten to target)
    for (name, value) in headers.iter() {
        let key = name.as_str();
        if key != "host" {
            req_builder = req_builder.header(name, value);
        }
    }

    let req = req_builder
        .body(body)
        .map_err(|e| crate::AppError::ProxyError(format!("Failed to build request: {}", e)))?;

    let response = client
        .request(req)
        .await
        .map_err(|e| crate::AppError::ProxyError(format!("Request failed: {}", e)))?;

    let (parts, incoming_body) = response.into_parts();

    let body_bytes = incoming_body
        .collect()
        .await
        .map_err(|e| crate::AppError::ProxyError(format!("Failed to read response body: {}", e)))?
        .to_bytes();

    let mut response_builder = Response::builder().status(parts.status);

    // Forward response headers, skipping hop-by-hop headers
    // (we collected the body so transfer-encoding/content-length are wrong)
    for (name, value) in parts.headers.iter() {
        let key = name.as_str();
        match key {
            "transfer-encoding" | "content-length" | "connection" | "keep-alive" => continue,
            _ => { response_builder = response_builder.header(name, value); }
        }
    }

    let response = response_builder
        .body(Body::from(body_bytes))
        .map_err(|e| crate::AppError::ProxyError(format!("Failed to build response: {}", e)))?;

    Ok(response)
}
