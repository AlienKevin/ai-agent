import discord
from discord.ui import View, Button

class MCQView(View):
    def __init__(self, agent, question_text: str, correct_answers: list):
        super().__init__(timeout=None)
        self.agent = agent
        self.question_text = question_text
        self.correct_answers = correct_answers
        self.selected_options = set()  # Track selected options
        self.is_multiple_answer = len(correct_answers) > 1
        
        # Add a label to indicate if multiple answers are allowed
        self.add_item(discord.ui.Button(
            label="Multiple answers allowed" if self.is_multiple_answer else "Select one answer",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=0
        ))

    @discord.ui.button(label="A", style=discord.ButtonStyle.secondary, custom_id="mcq_A", row=1)
    async def button_a(self, interaction: discord.Interaction, button: Button):
        await self._toggle_option(interaction, button, "A")

    @discord.ui.button(label="B", style=discord.ButtonStyle.secondary, custom_id="mcq_B", row=1)
    async def button_b(self, interaction: discord.Interaction, button: Button):
        await self._toggle_option(interaction, button, "B")

    @discord.ui.button(label="C", style=discord.ButtonStyle.secondary, custom_id="mcq_C", row=1)
    async def button_c(self, interaction: discord.Interaction, button: Button):
        await self._toggle_option(interaction, button, "C")

    @discord.ui.button(label="D", style=discord.ButtonStyle.secondary, custom_id="mcq_D", row=2)
    async def button_d(self, interaction: discord.Interaction, button: Button):
        await self._toggle_option(interaction, button, "D")

    @discord.ui.button(label="E", style=discord.ButtonStyle.secondary, custom_id="mcq_E", row=2)
    async def button_e(self, interaction: discord.Interaction, button: Button):
        await self._toggle_option(interaction, button, "E")

    @discord.ui.button(label="Not Sure", style=discord.ButtonStyle.danger, custom_id="mcq_not_sure", row=3)
    async def button_not_sure(self, interaction: discord.Interaction, button: Button):
        await self._handle_answer(interaction, "NOT SURE")

    @discord.ui.button(label="Submit Answer", style=discord.ButtonStyle.success, custom_id="mcq_submit", row=3)
    async def submit_button(self, interaction: discord.Interaction, button: Button):
        if not self.selected_options:
            await interaction.response.send_message("Please select at least one option before submitting.", ephemeral=True)
            return
            
        # Convert set to sorted list for consistent display
        selected_list = sorted(list(self.selected_options))
        
        # If only one answer is selected but multiple are allowed, that's fine
        # If only one answer is expected but multiple are selected, we'll still process it
        await self._handle_answer(interaction, selected_list)

    async def _toggle_option(self, interaction: discord.Interaction, button: Button, option: str):
        try:
            """Toggle selection of an option"""
            if option in self.selected_options:
                self.selected_options.remove(option)
                button.style = discord.ButtonStyle.secondary
            else:
                # If not multiple answer, clear previous selections
                if not self.is_multiple_answer:
                    self.selected_options.clear()
                    # Reset all buttons to secondary style
                    for child in self.children:
                        if isinstance(child, discord.ui.Button) and child.custom_id and child.custom_id.startswith("mcq_") and len(child.custom_id) == 5:
                            child.style = discord.ButtonStyle.secondary
                
                self.selected_options.add(option)
                button.style = discord.ButtonStyle.primary
                
            await interaction.response.edit_message(view=self)
        except Exception as e:
            print(f"Error in _toggle_option: {e}")
            await interaction.followup.send(
                "I encountered an error processing your answer. Please try again.",
                ephemeral=True
            )

    async def _handle_answer(self, interaction: discord.Interaction, answer):
        """Base implementation to be overridden by subclasses"""
        await interaction.response.send_message(
            "This method should be overridden by subclasses.",
            ephemeral=True
        )
