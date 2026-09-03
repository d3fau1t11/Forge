import os
import zipfile

def create_forensics_fixture(output_path="tests/fixtures/evidence_sample.zip") -> str:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    flag_content = "FORGE CTF Forensics Challenge File\nEmbedded Secrets:\nFLAG{forge_forensics_strings_found_9999}\n"
    txt_filename = "secrets.txt"
    
    with zipfile.ZipFile(output_path, "w") as z:
        z.writestr(txt_filename, flag_content)
        
    return output_path

if __name__ == "__main__":
    path = create_forensics_fixture()
    print(f"Created Forensics Fixture at: {path}")
