fn main() {
    let output = std::process::Command::new("rustc")
        .arg("--version")
        .output()
        .expect("failed to run rustc --version");
    let full = String::from_utf8(output.stdout).unwrap();
    let version = full
        .trim()
        .strip_prefix("rustc ")
        .unwrap_or(full.trim())
        .split_whitespace()
        .next()
        .unwrap_or("unknown");
    println!("cargo:rustc-env=RUSTC_VERSION={}", version);
}
