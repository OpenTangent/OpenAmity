from core.cerebrum import Tool
from core.address_book import AddressBookManager

class ContactsTool(Tool):
    name = "Contacts"
    description = "Manage and look up people in the agent's Address Book."
    commands = ["add_contact", "update_contact", "delete_contact", "lookup_contact", "list_contacts"]

    def __init__(self):
        self.manager = AddressBookManager()

    def execute(self, command: str, *args, **kwargs) -> str:
        if command == "add_contact":
            phone_number = kwargs.get("phone_number")
            name = kwargs.get("name")
            relationship = kwargs.get("relationship", "")
            if not phone_number or not name:
                return "Error: phone_number and name are required."
            success, msg = self.manager.add_contact(phone_number, name, relationship)
            return msg
            
        elif command == "update_contact":
            phone_number = kwargs.get("phone_number")
            name = kwargs.get("name")
            relationship = kwargs.get("relationship")
            if not phone_number:
                return "Error: phone_number is required."
            success, msg = self.manager.update_contact(phone_number, name=name, relationship=relationship)
            return msg
            
        elif command == "delete_contact":
            phone_number = kwargs.get("phone_number")
            if not phone_number:
                return "Error: phone_number is required."
            success, msg = self.manager.delete_contact(phone_number)
            return msg
            
        elif command == "lookup_contact":
            phone_number = kwargs.get("phone_number")
            name = kwargs.get("name")
            
            if phone_number:
                res = self.manager.lookup_by_number(phone_number)
                if res:
                    return f"Found: {res['name']} ({res['phone_number']}) - Relationship: {res.get('relationship', 'None')}"
                return f"No contact found for number: {phone_number}"
                
            if name:
                results = self.manager.lookup_by_name(name)
                if results:
                    output = [f"- {c['name']} ({c['phone_number']}) - Rel: {c.get('relationship', 'None')}" for c in results]
                    return "Found matches:\n" + "\n".join(output)
                return f"No contact found for name: {name}"
                
            return "Error: Please provide either phone_number or name to lookup."
            
        elif command == "list_contacts":
            contacts = self.manager.list_all()
            if not contacts:
                return "Address book is empty."
            output = [f"- {c['name']} ({c['phone_number']}) - Rel: {c.get('relationship', 'None')}" for c in contacts]
            return "Address Book:\n" + "\n".join(output)

        return f"Unknown command: {command}"

    def get_tool_declarations(self) -> list:
        return [
            {
                "name": "Contacts_add_contact",
                "description": "Add a new contact to the address book.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "phone_number": {"type": "STRING", "description": "The phone number of the contact."},
                        "name": {"type": "STRING", "description": "The full name of the contact."},
                        "relationship": {"type": "STRING", "description": "Optional tag describing the relationship (e.g. 'Friend', 'Work')."}
                    },
                    "required": ["phone_number", "name"]
                }
            },
            {
                "name": "Contacts_update_contact",
                "description": "Update an existing contact in the address book.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "phone_number": {"type": "STRING", "description": "The phone number of the contact to update."},
                        "name": {"type": "STRING", "description": "The new name (optional)."},
                        "relationship": {"type": "STRING", "description": "The new relationship tag (optional)."}
                    },
                    "required": ["phone_number"]
                }
            },
            {
                "name": "Contacts_delete_contact",
                "description": "Delete a contact from the address book.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "phone_number": {"type": "STRING", "description": "The phone number of the contact to delete."}
                    },
                    "required": ["phone_number"]
                }
            },
            {
                "name": "Contacts_lookup_contact",
                "description": "Look up a contact by their phone number or name.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "phone_number": {"type": "STRING", "description": "The phone number to search for."},
                        "name": {"type": "STRING", "description": "The name to search for."}
                    }
                }
            },
            {
                "name": "Contacts_list_contacts",
                "description": "List all contacts in the address book.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            }
        ]
