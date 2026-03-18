#!/usr/bin/env python3
"""
Certificate Generator for QUIC Demo
Generates self-signed certificates in the same directory as the server/client.
"""

import os
import sys
import datetime
from pathlib import Path

def generate_certificates():
    """Generate self-signed certificates for QUIC testing."""
    print("🔐 Generating SSL certificates for QUIC demo...")
    
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        print("❌ Cryptography library not found. Installing...")
        os.system(f"{sys.executable} -m pip install cryptography")
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    
    # Check if certificates already exist
    cert_path = Path("cert.pem")
    key_path = Path("key.pem")
    
    if cert_path.exists() and key_path.exists():
        print("📁 Certificates already exist:")
        print(f"   - {cert_path} (exists)")
        print(f"   - {key_path} (exists)")
        
        # Check expiration
        try:
            with open(cert_path, "rb") as f:
                cert_data = f.read()
            cert = x509.load_pem_x509_certificate(cert_data)
            expires = cert.not_valid_after
            days_left = (expires - datetime.datetime.now(expires.tzinfo)).days
            
            if days_left > 30:
                print(f"✅ Certificates are valid for another {days_left} days")
                response = input("Do you want to regenerate them anyway? (y/N): ")
                if response.lower() != 'y':
                    print("✅ Using existing certificates")
                    return True
            else:
                print(f"⚠️  Certificates expire in {days_left} days")
                response = input("Regenerate now? (Y/n): ")
                if response.lower() == 'n':
                    print("⚠️  Continuing with expiring certificates...")
                    return True
        except Exception:
            print("⚠️  Could not read certificate expiration")
    
    print("🆕 Generating new certificates...")
    
    # Generate private key
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    # Save private key
    with open("key.pem", "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    print("✅ Generated: key.pem")
    
    # Create self-signed certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])
    
    # Set validity: 2 years for testing
    valid_for = datetime.timedelta(days=730)  # 2 years
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + valid_for
    ).add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.DNSName("127.0.0.1"),
            x509.DNSName("::1"),
        ]),
        critical=False,
    ).add_extension(
        x509.BasicConstraints(ca=False, path_length=None),
        critical=True,
    ).sign(key, hashes.SHA256())
    
    # Save certificate
    with open("cert.pem", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print("✅ Generated: cert.pem")
    
    # Create a combined file for easier loading (optional)
    with open("combined.pem", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
        f.write(b"\n")
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ))
    print("✅ Generated: combined.pem (cert + key)")
    
    # Set appropriate permissions
    os.chmod("key.pem", 0o600)
    os.chmod("combined.pem", 0o600)
    
    print("\n📋 Certificate Details:")
    print(f"   - Common Name: localhost")
    print(f"   - Valid from: {cert.not_valid_before}")
    print(f"   - Valid until: {cert.not_valid_after}")
    print(f"   - Days valid: {valid_for.days}")
    print(f"   - Algorithm: RSA 2048-bit")
    
    return True

if __name__ == "__main__":
    success = generate_certificates()
    sys.exit(0 if success else 1)