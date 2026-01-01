from discord import ActionRow, Message
from discord.components import Button as ButtonComponent
from discord.components import SelectMenu as SelectMenuComponent
from discord.ui import Button, Select, View


class Interface(View):
    @classmethod
    def from_message(cls, message: Message):
        view = cls()
        for component in message.components:
            if isinstance(component, ActionRow):
                for child in component.children:
                    if isinstance(child, ButtonComponent):
                        view.add_item(Button.from_component(child))

                    elif isinstance(child, SelectMenuComponent):
                        view.add_item(Select.from_component(child))

        return view
