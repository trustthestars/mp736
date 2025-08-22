import os
import json
import asyncio
import aiohttp
import random
from typing import Dict, Any, AsyncGenerator

# Configuration
FHIR_ENDPOINT = os.getenv("FHIR_ENDPOINT", "https://api.acme-hospital.example/fhir")

class MockMessage:
    def __init__(self, data: Dict[str, Any], subject: str):
        self.data = json.dumps(data).encode()
        self.subject = subject
    
    async def ack(self):
        print("✓ Message acknowledged")
    
    async def nak(self, delay: int = 0):
        print(f"✗ Negative acknowledgment received, will retry after {delay} seconds")
    
    async def term(self):
        print("✗ Message terminated")

async def mock_subscribe() -> AsyncGenerator[MockMessage, None]:
    """Generate mock messages for testing"""
    sample_messages = [
        {"payload": {"resourceType": "Bundle", "type": "message"}, "subject": "sector.health.1"},
        {"payload": {"resourceType": "Bundle", "type": "diagnostic"}, "subject": "sector.health.2"},
        {"payload": {"resourceType": "Bundle", "type": "observation"}, "subject": "sector.health.3"},
    ]
    
    while True:  # Keep generating messages
        for msg in sample_messages:
            yield MockMessage(msg, msg["subject"])
            await asyncio.sleep(2)  # Simulate delay between messages

async def process_messages():
    """Process messages from the mock queue"""
    print("Starting mock message processor. Press Ctrl+C to exit.")
    
    try:
        async for msg in mock_subscribe():
            try:
                print(f"\n📨 Received message on {msg.subject}")
                body = json.loads(msg.data)
                
                # Simulate processing
                print(f"🔄 Processing: {json.dumps(body, indent=2)}")
                
                # Simulate FHIR server request
                success = random.random() > 0.3  # 70% success rate
                if success:
                    print("✅ Successfully processed message")
                    await msg.ack()
                else:
                    print("❌ Simulated processing error")
                    await msg.nak(delay=5)
                    
            except json.JSONDecodeError as e:
                print(f"❌ Failed to decode message: {e}")
                await msg.term()
            except Exception as e:
                print(f"❌ Error processing message: {e}")
                await msg.term()
                
    except asyncio.CancelledError:
        print("\n🛑 Shutdown signal received...")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        raise

async def main():
    print("🚀 Starting mock message processor...")
    print(f"📤 Messages will be forwarded to: {FHIR_ENDPOINT}")
    print("ℹ️  Press Ctrl+C to exit\n")
    
    try:
        await process_messages()
    except asyncio.CancelledError:
        print("\n🛑 Shutting down gracefully...")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Shutting down gracefully...")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        raise
