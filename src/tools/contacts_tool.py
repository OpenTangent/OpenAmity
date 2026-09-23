from core.cerebrum import Tool
from core.address_book import AddressBookManager


class ContactsTool(Tool):
    name = "Contacts"
    icon = "👤"
    color = "#009688"
    async_commands = []
    description = "Manage and look up people in the agent's Address Book."
    commands = ["add_contact", "update_contact",
                "delete_contact", "lookup_contact", "list_contacts"]

    def __init__(self, orchestrator=None):
        super().__init__(orchestrator)
        agent_id = self.orchestrator.agent_id if self.orchestrator else None
        self.manager = AddressBookManager(agent_id=agent_id)

    def execute(self, command: str, *args, **kwargs) -> str:
        def format_contact(c):
            base = f"{c['name']} ({c['phone_number']}) - Rel: {c.get('relationship', 'None')}"
            if c.get("custom_fields"):
                base += f" | Custom Data: {c['custom_fields']}"
            return base

        if command == "add_contact":
            phone_number = kwargs.get("phone_number")
            name = kwargs.get("name")
            relationship = kwargs.get("relationship", "")
            custom_fields = kwargs.get("custom_fields")
            if not phone_number or not name:
                return "Error: phone_number and name are required."
            success, msg = self.manager.add_contact(
                phone_number, name, relationship, custom_fields)
            return msg

        elif command == "update_contact":
            phone_number = kwargs.get("phone_number")
            name = kwargs.get("name")
            relationship = kwargs.get("relationship")
            custom_fields = kwargs.get("custom_fields")
            if not phone_number:
                return "Error: phone_number is required."
            success, msg = self.manager.update_contact(
                phone_number, name=name, relationship=relationship, custom_fields=custom_fields)
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
            search_query = kwargs.get("search_query")

            if phone_number:
                res = self.manager.lookup_by_number(phone_number)
                if res:
                    return f"Found: {format_contact(res)}"
                return f"No contact found for number: {phone_number}"

            if search_query:
                results = self.manager.search(search_query)
                if results:
                    output = ["- " + format_contact(c) for c in results]
                    return "Found matches for search query:\n" + "\n".join(output)
                return f"No contact found for query: {search_query}"

            if name:
                results = self.manager.lookup_by_name(name)
                if results:
                    output = ["- " + format_contact(c) for c in results]
                    return "Found matches:\n" + "\n".join(output)
                return f"No contact found for name: {name}"

            return "Error: Please provide phone_number, name, or search_query to lookup."

        elif command == "list_contacts":
            contacts = self.manager.list_all()
            if not contacts:
                return "Address book is empty."
            output = ["- " + format_contact(c) for c in contacts]
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
                        "relationship": {"type": "STRING", "description": "Optional tag describing the relationship (e.g. 'Friend', 'Work')."},
                        "custom_fields": {"type": "OBJECT", "description": "Optional key-value pairs for dynamic data like nickname, company, address, etc."}
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
                        "relationship": {"type": "STRING", "description": "The new relationship tag (optional)."},
                        "custom_fields": {"type": "OBJECT", "description": "Optional key-value pairs to update or add. Pass null as value to remove a field."}
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
                "description": "Look up a contact by their phone number, name, or global search query.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "phone_number": {"type": "STRING", "description": "The phone number to search for."},
                        "name": {"type": "STRING", "description": "The name to search for."},
                        "search_query": {"type": "STRING", "description": "Global search term to look through all basic fields and dynamic custom fields with partial matching."}
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
