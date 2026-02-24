import discord

fila = []

class QueueView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Entrar na Fila", style=discord.ButtonStyle.green)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user in fila:
            await interaction.response.send_message("Já está na fila.", ephemeral=True)
            return

        fila.append(interaction.user)
        await interaction.response.send_message("Entrou na fila.", ephemeral=True)

        if len(fila) == 10:
            await interaction.channel.send("Fila completa! Criando lobby...")
