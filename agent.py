from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import os
load_dotenv()

async def goal_based_agent(system_prompt: str):
    return create_agent(
        model=ChatOpenAI(
            base_url='http://localhost:3001/v1',
            model='automatic',
            api_key=os.getenv('FREELLM_API_KEY')
        ),
        system_prompt=system_prompt
    )