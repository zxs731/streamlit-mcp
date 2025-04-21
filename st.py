import asyncio  
from contextlib import AsyncExitStack  
import json  
import os  
import httpx  
import streamlit as st  
from dotenv import load_dotenv  
from mcp import ClientSession, StdioServerParameters  
from mcp.client.stdio import stdio_client  
from mcp.client.sse import sse_client  
from openai import AsyncOpenAI  


  
# Load environment variables  
load_dotenv("./ds.env")  
model = os.getenv("model")  
  
class MCPClient:  
    def __init__(self):  
        self.session = None  
        self.sessions = {}  
        self.exit_stack = AsyncExitStack()  
        self.tools = []  
        self.messages = []  
        self.client = AsyncOpenAI(  
            base_url=os.environ["base_url"],  
            api_key=os.environ["api_key"]
        )  
  
    async def cleanup(self):  
        await self.exit_stack.aclose()  
  
    async def connect_to_server(self):  
        with open("mcp_server_config.json", "r") as f:  
            config = json.load(f)  
        conf = config["mcpServers"]  
        self.tools = []  
        for key in conf.keys():  
            v = conf[key]  
            print(v)
            session = None  
            if "baseUrl" in v and v['isActive']:  
                server_url = v['baseUrl']  
                sse_transport = await self.exit_stack.enter_async_context(sse_client(server_url))  
                write, read = sse_transport  
                session = await self.exit_stack.enter_async_context(ClientSession(write, read))  
            elif "command" in v and v['isActive']:  
                command = v['command']  
                args = v['args']  
                server_params = StdioServerParameters(command=command, args=args, env=None)  
                stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))  
                stdio1, write1 = stdio_transport  
                session = await self.exit_stack.enter_async_context(ClientSession(stdio1, write1))  
  
            if session:  
                await session.initialize()  
                response = await session.list_tools()  
                tools = response.tools  
                for tool in tools:  
                    self.sessions[tool.name] = session  
                self.tools += tools  
  
    async def run_conversation(self, messages, tools,function_handle=None,content_handle=None):  
        response_message = await self.client.chat.completions.create(  
            model=model,  
            messages=messages,  
            tools=tools,  
            stream=True  
        )  
        content = ''  
        function_list = [] 
        content_str="" 
        async for chunk in response_message:  
            if chunk and len(chunk.choices) > 0:  
                chunk_message = chunk.choices[0].delta  
                if chunk_message.content:  
                    content += chunk_message.content
                    if content_handle:
                        content_str+=chunk_message.content
                        content_handle(content_str)  
                if chunk_message.tool_calls: 
                    for tool_call in chunk_message.tool_calls:
                        if len(function_list) < tool_call.index + 1:  
                            function_list.append({'name': '', 'args': '', 'id': tool_call.id})  
                        if tool_call and tool_call.function.name:  
                            function_list[tool_call.index]['name'] += tool_call.function.name  
                        if tool_call and tool_call.function.arguments:  
                            function_list[tool_call.index]['args'] += tool_call.function.arguments  
                            
        if len(function_list) > 0:  
            findex = 0  
            tool_calls = []  
            temp_messages = []
            think_str=""
            for func in function_list:  
                function_name = func["name"]  
                print(function_name)
                function_args = func["args"]
                function_args = json.loads(function_args)
                toolid = func["id"]  
                if function_name != '': 
                    # 执行工具调用
                    function_response = await self.sessions[function_name].call_tool(function_name, function_args)
                    print(f"⏳MCP: [Calling tool {function_name} with args {function_args}]")
                    if function_handle:
                        think_str+=f"⏳MCP: [Calling tool {function_name} with args {function_args}]\r\nResult: {function_response.content[0].text}\r\n"
                        function_handle(think_str)

                    tool_calls.append({"id": toolid, "function": {"arguments": func["args"], "name": function_name}, "type": "function", "index": findex})  
                    temp_messages.append({  
                        "tool_call_id": toolid,  
                        "role": "tool",  
                        "name": function_name,  
                        "content": function_response.content[0].text,  
                    })  
                    findex += 1
            messages.append({"role": "assistant", "tool_calls": tool_calls})  
            for m in temp_messages:  
                messages.append(m) 
            print(messages)
            return await self.run_conversation(messages, tools,function_handle,content_handle)  
        elif content != '':  
            messages.append({"role": "assistant", "content": content})  
            return messages[-1] 
  
    async def process_query(self, query: str,function_handle=None,content_handle=None): 
        self.messages=st.session_state.messages
        #self.messages.append({"role": "user", "content": query})  
        messages = self.messages[-20:]  
        available_tools = [{  
            "type": "function",  
            "function": {  
                "name": tool.name,  
                "description": tool.description,  
                "parameters": tool.inputSchema  
            }  
        } for tool in self.tools]  
        reply_message = await self.run_conversation(messages, available_tools,function_handle,content_handle)  
        #self.messages.append(reply_message)  
        return reply_message  
  
# Streamlit UI  
st.title("Deepseek MCP Client")  

@st.cache_resource
def initClient():
    client = MCPClient()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(client.connect_to_server())
    return client, loop

client,loop=initClient()

if "messages" not in st.session_state:
    st.session_state.messages = []
  
for message1 in st.session_state.messages:
    with st.chat_message(message1["role"]):
        c=message1["content"]
        
        st.markdown(c)

def writeReply(cont,msg):
    cont.write(msg)
def writeThinkReply(exp,msg):
    exp.write(msg)


if prompt := st.chat_input():
    st.chat_message("user").write(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("assistant"):
        with st.expander("Think", expanded=False):
            exp=st.empty()
            exp.text("⏳...")
        p=st.empty()
        p.text("⏳...")
        res = loop.run_until_complete(client.process_query(prompt,lambda x:exp.write(x),lambda x:p.write(x)))
        res=res['content']
        print(res)
        #p.write(res)

        st.session_state.messages.append({"role": "assistant", "content": res})

