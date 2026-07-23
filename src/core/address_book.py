import os
import json

class AddressBookManager:
    def __init__(self, filepath=None):
        if not filepath:
            from config import paths
            filepath = os.path.join(paths.get_app_data_dir(), "address_book.json")
        self.filepath = filepath
        self.ensure_file()

    def ensure_file(self):
        if not os.path.exists(self.filepath):
            os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
            self.save_data({"contacts": []})

    def load_data(self):
        try:
            with open(self.filepath, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading address book: {e}")
            return {"contacts": []}

    def save_data(self, data):
        try:
            with open(self.filepath, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving address book: {e}")

    def add_contact(self, phone_number, name, relationship=""):
        data = self.load_data()
        
        # Clean phone number (strip whitespace, ensure starts with + if needed, etc)
        phone_number = phone_number.replace(" ", "").strip()
        
        # Check if exists
        for c in data["contacts"]:
            if c["phone_number"] == phone_number:
                return False, f"Contact with number {phone_number} already exists."
                
        contact = {
            "phone_number": phone_number,
            "name": name,
            "relationship": relationship
        }
        data["contacts"].append(contact)
        self.save_data(data)
        return True, f"Contact {name} ({phone_number}) added successfully."

    def update_contact(self, phone_number, name=None, relationship=None):
        data = self.load_data()
        phone_number = phone_number.replace(" ", "").strip()
        
        for c in data["contacts"]:
            if c["phone_number"] == phone_number:
                if name is not None:
                    c["name"] = name
                if relationship is not None:
                    c["relationship"] = relationship
                self.save_data(data)
                return True, f"Contact {phone_number} updated."
                
        return False, f"Contact with number {phone_number} not found."

    def delete_contact(self, phone_number):
        data = self.load_data()
        phone_number = phone_number.replace(" ", "").strip()
        
        initial_count = len(data["contacts"])
        data["contacts"] = [c for c in data["contacts"] if c["phone_number"] != phone_number]
        
        if len(data["contacts"]) < initial_count:
            self.save_data(data)
            return True, f"Contact {phone_number} deleted."
        return False, f"Contact with number {phone_number} not found."

    def lookup_by_number(self, phone_number):
        data = self.load_data()
        
        # Remove common characters, WhatsApp suffixes, and leading zeros
        def clean_num(n):
            n = n.replace(" ", "").replace("+", "").replace("-", "")
            n = n.replace("@c.us", "").replace("@g.us", "").replace("@lid", "")
            return n.lstrip('0')
            
        p_clean = clean_num(phone_number)
        
        for c in data["contacts"]:
            c_clean = clean_num(c["phone_number"])
            if c_clean == p_clean or p_clean.endswith(c_clean) or c_clean.endswith(p_clean):
                return c
        return None

    def lookup_by_name(self, name):
        data = self.load_data()
        results = []
        name_lower = name.lower()
        for c in data["contacts"]:
            if name_lower in c["name"].lower():
                results.append(c)
        return results

    def list_all(self):
        return self.load_data().get("contacts", [])
