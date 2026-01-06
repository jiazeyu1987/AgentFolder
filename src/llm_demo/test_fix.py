#!/usr/bin/env python3
"""
Quick test to verify the anthropic upgrade fixed the proxies error
"""
import sys
import traceback

def test_import():
    """Test if the module imports without the proxies error"""
    print("Testing module import...")
    try:
        from llm_communication import SimpleLLMService, LLMResponse, get_llm_service
        print("SUCCESS: Module imported successfully!")
        print(f"  - SimpleLLMService: {SimpleLLMService}")
        print(f"  - LLMResponse: {LLMResponse}")
        print(f"  - get_llm_service: {get_llm_service}")

        # Test service initialization
        print("\nTesting service initialization...")
        service = SimpleLLMService()
        print("SUCCESS: Service initialized successfully!")
        print(f"  - Default model: {service.default_model}")
        print(f"  - Client: {service.client}")

        return True
    except TypeError as e:
        if "proxies" in str(e):
            print("FAILED: The proxies error still exists!")
            print(f"  Error: {e}")
            traceback.print_exc()
            return False
        else:
            print(f"FAILED: Different TypeError occurred: {e}")
            traceback.print_exc()
            return False
    except Exception as e:
        print(f"FAILED: Unexpected error: {e}")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_import()
    sys.exit(0 if success else 1)
