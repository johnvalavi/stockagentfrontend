
# ===============================================================================
# IMPORTS AND SETUP
# ===============================================================================

# CrewAI Flow framework for building multi-step AI workflows
from crewai.flow.flow import Flow, start, router, listen, or_
from litellm import completion
from pydantic import BaseModel
from typing import Literal, List

# AG UI types for message handling and state management
from ag_ui.core.types import AssistantMessage, ToolMessage
from ag_ui.core.events import StateDeltaEvent, EventType

# Standard Python libraries
import uuid  # For generating unique IDs
import asyncio  # For asynchronous operations
import json  # For JSON serialization/deserialization
import os  # For environment variables
from datetime import datetime  # For date handling

# External libraries
import google.generativeai as genai  # Google Generative AI client
from dotenv import load_dotenv  # Load environment variables from .env file
import yfinance as yf  # Yahoo Finance API for stock data
import numpy as np  # Numerical computations
import pandas as pd  # Data manipulation and analysis

# Import custom prompts for the AI models
from prompts import system_prompt, insights_prompt

# Load environment variables (like API keys) from .env file
load_dotenv()


# ===============================================================================
# TOOL DEFINITIONS
# ===============================================================================

# Tool definition for extracting investment data from user input
# This tool uses OpenAI's function calling feature to parse user queries
# and extract structured data like stock symbols, investment amounts, etc.
extract_relevant_data_from_user_prompt = {
    "type": "function",  # Required field for OpenAI function calling
    "function": {
        "name": "extract_relevant_data_from_user_prompt",
        "description": "Gets the data like ticker symbols, amount of dollars to be invested, interval of investment.",
        "parameters": {
            "type": "object",
            "properties": {
                # List of stock ticker symbols (e.g., ['AAPL', 'GOOGL'])
                "ticker_symbols": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                    "description": "A list of stock ticker symbols, e.g. ['AAPL', 'GOOGL']."
                },
                # Date when the investment should start
                "investment_date": {
                    "type": "string",
                    "description": "The date of investment, e.g. '2023-01-01'.",
                    "format": "date"
                },
                # Amount of money to invest in each stock (parallel array to ticker_symbols)
                "amount_of_dollars_to_be_invested": {
                    "type": "array",
                    "items": {
                        "type": "number"
                    },
                    "description": "The amount of dollars to be invested, e.g. [10000, 20000, 30000]."
                },
                # Investment strategy: single purchase or dollar-cost averaging over time
                "interval_of_investment": {
                    "type": "string",
                    "description": "The interval of investment, e.g. '1d', '5d', '1mo', '3mo', '6mo', '1y'. If the user did not specify the interval, assume it as 'single_shot'.",
                    "enum": ["1d", "5d", "7d", "1mo", "3mo", "6mo", "1y", "2y", "3y", "4y", "5y", "single_shot"]
                },
                # Whether to add to actual portfolio or sandbox/test portfolio
                "to_be_added_in_portfolio": {
                    "type": "boolean",
                    "description": "True if the user wants to add it to the current portfolio; false if they want to add it to the sandbox portfolio."
                }
            },
            # These fields are required for the tool to function properly
            "required": [
                "ticker_symbols",
                "investment_date",
                "amount_of_dollars_to_be_invested",
                "to_be_added_in_portfolio"
            ]
        }
    }
}

# Tool definition for generating bull/bear insights about stocks or portfolios
# This tool generates positive (bullish) and negative (bearish) analysis
# to provide balanced perspective on investment decisions
generate_insights = {
  "type": "function",
  "function": {
    "name": "generate_insights",
    "description": "Generate positive (bull) and negative (bear) insights for a stock or portfolio.",
    "parameters": {
      "type": "object",
      "properties": {
        # Positive insights (reasons why the investment might perform well)
        "bullInsights": {
          "type": "array",
          "description": "A list of positive insights (bull case) for the stock or portfolio.",
          "items": {
            "type": "object",
            "properties": {
              "title": {
                "type": "string",
                "description": "Short title for the positive insight."
              },
              "description": {
                "type": "string",
                "description": "Detailed description of the positive insight."
              },
              "emoji": {
                "type": "string",
                "description": "Emoji representing the positive insight."
              }
            },
            "required": ["title", "description", "emoji"]
          }
        },
        # Negative insights (potential risks or concerns about the investment)
        "bearInsights": {
          "type": "array",
          "description": "A list of negative insights (bear case) for the stock or portfolio.",
          "items": {
            "type": "object",
            "properties": {
              "title": {
                "type": "string",
                "description": "Short title for the negative insight."
              },
              "description": {
                "type": "string",
                "description": "Detailed description of the negative insight."
              },
              "emoji": {
                "type": "string",
                "description": "Emoji representing the negative insight."
              }
            },
            "required": ["title", "description", "emoji"]
          }
        }
      },
      "required": ["bullInsights", "bearInsights"]
    }
  }
}
# ===============================================================================
# MAIN FLOW CLASS
# ===============================================================================

class StockAnalysisFlow(Flow):
    """
    Main workflow class that orchestrates the stock analysis process.
    This flow consists of multiple stages:
    1. start() - Initialize the system prompt with portfolio data
    2. chat() - Parse user input and extract investment parameters
    3. simulation() - Gather historical stock data
    4. allocation() - Calculate portfolio allocation and performance
    5. insights() - Generate bull/bear insights about the investments
    6. end() - Return final state
    """
    
    @start()
    def start(self):
        """
        Step 1: Initialize the workflow
        - Replace placeholder in system prompt with actual portfolio data
        - This sets up the AI assistant with context about the current portfolio
        """
        # Inject current portfolio data into the system prompt template
        self.state['state']["messages"][0].content = system_prompt.replace(
            "{PORTFOLIO_DATA_PLACEHOLDER}", json.dumps(self.state["investment_portfolio"])
        )
        return self.state

    @listen("start")
    async def chat(self):
        """
        Step 2: Parse user input and extract investment parameters
        - Create a tool log entry to show progress to the user
        - Use OpenAI to analyze the user's message and extract structured data
        - Return next step based on whether structured data was extracted
        """
        try:
            # Step 2.1: Create a new tool log entry to track progress
            tool_log_id = str(uuid.uuid4())
            self.state['state']["tool_logs"].append(
                {
                    "id": tool_log_id,
                    "message": "Analyzing user query",
                    "status": "processing",
                }
            )
            
            # Step 2.2: Emit state change event to update UI with new tool log
            self.state.get("emit_event")(
                StateDeltaEvent(
                    type=EventType.STATE_DELTA,
                    delta=[
                        {
                            "op": "add",
                            "path": "/tool_logs/-",
                            "value": {
                                "message": "Analyzing user query",
                                "status": "processing",
                                "id": tool_log_id,
                            },
                        }
                    ],
                )
            )
            await asyncio.sleep(0)  # Allow other tasks to run
            
            # Step 2.3: Call Gemini to analyze user input and extract investment data
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel(
                model_name="gemini-2.0-flash-exp",
                tools=[extract_relevant_data_from_user_prompt]
            )

            # Convert messages to Gemini format
            gemini_messages = []
            for msg in self.state['state']['messages']:
                if msg.role == "user":
                    gemini_messages.append({"role": "user", "parts": [msg.content]})
                elif msg.role == "assistant":
                    gemini_messages.append({"role": "model", "parts": [msg.content]})

            response = model.generate_content(
                gemini_messages,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,
                )
            )
            
            # Step 2.4: Update tool log status to completed
            index = len(self.state['state']["tool_logs"]) - 1
            self.state.get("emit_event")(
                StateDeltaEvent(
                    type=EventType.STATE_DELTA,
                    delta=[
                        {
                            "op": "replace",
                            "path": f"/tool_logs/{index}/status",
                            "value": "completed",
                        }
                    ],
                )
            )
            await asyncio.sleep(0)
            
            # Step 2.5: Check if Gemini extracted structured data via function calling
            if response.candidates and response.candidates[0].function_calls:
                # Convert function calls to our internal format
                tool_calls = []
                for fc in response.candidates[0].function_calls:
                    tool_calls.append({
                        "id": str(uuid.uuid4()),
                        "type": "function",
                        "function": {
                            "name": fc.name,
                            "arguments": json.dumps(fc.args),
                        },
                    })

                # Create assistant message with tool calls
                a_message = AssistantMessage(
                    role="assistant", tool_calls=tool_calls, id=str(uuid.uuid4())
                )
                self.state['state']["messages"].append(a_message)
                return "simulation"  # Proceed to data gathering step
            else:
                # No structured data extracted, just respond with regular message
                content = ""
                if response.candidates and response.candidates[0].content.parts:
                    content = response.candidates[0].content.parts[0].text

                a_message = AssistantMessage(
                    id=str(uuid.uuid4()),
                    content=content,
                    role="assistant",
                )
                self.state['state']["messages"].append(a_message)
                return "end"  # Skip to end since no investment data to process

            
        except Exception as e:
            # Step 2.6: Handle any errors during processing
            print(e)
            a_message = AssistantMessage(id=str(uuid.uuid4()), content="", role="assistant")
            self.state['state']["messages"].append(a_message)
            return "end"
    
    
    @listen("chat")
    async def simulation(self):
        """
        Step 3: Gather historical stock data for analysis
        - Extract investment parameters from the previous step
        - Download historical stock price data from Yahoo Finance
        - Prepare data for portfolio simulation
        """
        # Step 3.1: Ensure we have tool calls with investment data
        if self.state['state']['messages'][-1].tool_calls is None:
            return "end"
            
        # Step 3.2: Create tool log entry for stock data gathering
        tool_log_id = str(uuid.uuid4())
        self.state['state']["tool_logs"].append(
            {
                "id": tool_log_id,
                "message": "Gathering Stock Data",
                "status": "processing",
            }
        )
        
        # Step 3.3: Emit state change to update UI
        self.state.get("emit_event")(
            StateDeltaEvent(
                type=EventType.STATE_DELTA,
                delta=[
                    {
                        "op": "add",
                        "path": "/tool_logs/-",
                        "value": {
                            "message": "Gathering Stock Data",
                            "status": "processing",
                            "id": tool_log_id,
                        },
                    }
                ],
            )
        )
        await asyncio.sleep(0)
        
        # Step 3.4: Parse the extracted investment arguments from previous step
        arguments = json.loads(self.state['state']["messages"][-1].tool_calls[0].function.arguments)
        
        # Step 3.5: Create investment portfolio structure for UI display
        self.investment_portfolio = json.dumps(
            [
                {
                    "ticker": ticker,
                    "amount": arguments["amount_of_dollars_to_be_invested"][index],
                }
                for index, ticker in enumerate(arguments["ticker_symbols"])
            ]
        )
        
        # Step 3.6: Update state with new investment portfolio
        self.state.get("emit_event")(
            StateDeltaEvent(
                type=EventType.STATE_DELTA,
                delta=[
                    {
                        "op": "replace",
                        "path": f"/investment_portfolio",
                        "value": json.loads(self.investment_portfolio),
                    }
                ],
            )
        )
        await asyncio.sleep(2)
        
        # Step 3.7: Extract investment parameters
        tickers = arguments["ticker_symbols"]
        investment_date = arguments["investment_date"]
        current_year = datetime.now().year
        
        # Step 3.8: Validate and adjust investment date (limit to 4 years for data availability)
        if current_year - int(investment_date[:4]) > 4:
            print("investment date is more than 4 years ago")
            investment_date = f"{current_year - 4}-01-01"
            
        # Step 3.9: Calculate appropriate history period for data download
        if current_year - int(investment_date[:4]) == 0:
            history_period = "1y"
        else:
            history_period = f"{current_year - int(investment_date[:4])}y"

        # Step 3.10: Download historical stock data using Yahoo Finance
        data = yf.download(
            tickers,
            period=history_period,
            interval="3mo",  # Quarterly data points
            start=investment_date,
            end=datetime.today().strftime("%Y-%m-%d"),
        )
        
        # Step 3.11: Extract closing prices and store data for next step
        self.be_stock_data = data["Close"]  # Store closing prices DataFrame
        self.be_arguments = arguments  # Store extracted arguments for next step
        
        # Step 3.12: Mark stock data gathering as completed
        index = len(self.state['state']["tool_logs"]) - 1
        self.state.get("emit_event")(
            StateDeltaEvent(
                type=EventType.STATE_DELTA,
                delta=[
                    {
                        "op": "replace",
                        "path": f"/tool_logs/{index}/status",
                        "value": "completed",
                    }
                ],
            )
        )
        await asyncio.sleep(0)
        
        # Step 3.13: Proceed to portfolio allocation and simulation
        return "allocation"
    
    
    @listen("simulation")
    async def allocation(self):
        """
        Step 4: Calculate portfolio allocation and performance simulation
        - Simulate buying stocks based on investment strategy (single-shot vs DCA)
        - Calculate returns, allocation percentages, and performance metrics
        - Compare portfolio performance against SPY (S&P 500) benchmark
        - Generate performance data for charting
        """
        # Step 4.1: Ensure we have tool calls with investment data
        if self.state['state']['messages'][-1].tool_calls is None:
            return "end"
            
        # Step 4.2: Create tool log for allocation calculation
        tool_log_id = str(uuid.uuid4())
        # Step 4.2: Create tool log for allocation calculation
        tool_log_id = str(uuid.uuid4())
        self.state['state']["tool_logs"].append(
            {
                "id": tool_log_id,
                "message": "Calculating portfolio allocation",
                "status": "processing",
            }
        )
        
        # Step 4.3: Emit state change to update UI
        self.state.get("emit_event")(
            StateDeltaEvent(
                type=EventType.STATE_DELTA,
                delta=[
                    {
                        "op": "add",
                        "path": "/tool_logs/-",
                        "value": {
                            "message": "Allocating cash",
                            "status": "processing",
                            "id": tool_log_id,
                        },
                    }
                ],
            )
        )
        await asyncio.sleep(0)
        
        # Step 4.4: Extract data from previous steps
        stock_data = self.be_stock_data  # DataFrame: index=date, columns=tickers
        args = self.be_arguments
        tickers = args["ticker_symbols"]
        investment_date = args["investment_date"]
        amounts = args["amount_of_dollars_to_be_invested"]  # list, one per ticker
        interval = args.get("interval_of_investment", "single_shot")

        # Step 4.5: Initialize cash available for investment
        # Use existing available cash or sum of requested amounts
        if self.state['state']["available_cash"] is not None:
            total_cash = self.state['state']["available_cash"]
        else:
            total_cash = sum(amounts)
            
        # Step 4.6: Initialize tracking variables for simulation
        holdings = {ticker: 0.0 for ticker in tickers}  # Shares owned for each ticker
        investment_log = []  # Log of all investment transactions
        add_funds_needed = False  # Flag if more funds are needed
        add_funds_dates = []  # Dates when funds were insufficient

        # Step 4.7: Ensure stock data is sorted chronologically
        stock_data = stock_data.sort_index()

        # Step 4.8: Execute investment strategy based on interval
        if interval == "single_shot":
            # SINGLE-SHOT STRATEGY: Buy all shares at the first available date
            first_date = stock_data.index[0]
            row = stock_data.loc[first_date]
            
            # Loop through each ticker and attempt to buy allocated amount
            for idx, ticker in enumerate(tickers):
                price = row[ticker]
                
                # Step 4.8.1: Check if price data is available
                if np.isnan(price):
                    investment_log.append(
                        f"{first_date.date()}: No price data for {ticker}, could not invest."
                    )
                    add_funds_needed = True
                    add_funds_dates.append(
                        (str(first_date.date()), ticker, price, amounts[idx])
                    )
                    continue
                    
                # Step 4.8.2: Calculate how much to invest in this ticker
                allocated = amounts[idx]
                
                # Step 4.8.3: Check if we have enough cash and allocation is sufficient
                if total_cash >= allocated and allocated >= price:
                    shares_to_buy = allocated // price  # Integer division for whole shares
                    if shares_to_buy > 0:
                        cost = shares_to_buy * price
                        holdings[ticker] += shares_to_buy
                        total_cash -= cost
                        investment_log.append(
                            f"{first_date.date()}: Bought {shares_to_buy:.2f} shares of {ticker} at ${price:.2f} (cost: ${cost:.2f})"
                        )
                    else:
                        investment_log.append(
                            f"{first_date.date()}: Not enough allocated cash to buy {ticker} at ${price:.2f}. Allocated: ${allocated:.2f}"
                        )
                        add_funds_needed = True
                        add_funds_dates.append(
                            (str(first_date.date()), ticker, price, allocated)
                        )
                else:
                    # Step 4.8.4: Insufficient funds for this ticker
                    investment_log.append(
                        f"{first_date.date()}: Not enough total cash to buy {ticker} at ${price:.2f}. Allocated: ${allocated:.2f}, Available: ${total_cash:.2f}"
                    )
                    add_funds_needed = True
                    add_funds_dates.append(
                        (str(first_date.date()), ticker, price, total_cash)
                    )
            # No further purchases on subsequent dates for single-shot strategy
        else:
            # DOLLAR-COST AVERAGING (DCA) STRATEGY: Spread investments over time
            for date, row in stock_data.iterrows():
                for i, ticker in enumerate(tickers):
                    price = row[ticker]
                    
                    # Step 4.8.5: Skip if no price data available
                    if np.isnan(price):
                        continue  # skip if price is NaN
                        
                    # Step 4.8.6: Invest as much as possible for this ticker at this date
                    if total_cash >= price:
                        shares_to_buy = total_cash // price
                        if shares_to_buy > 0:
                            cost = shares_to_buy * price
                            holdings[ticker] += shares_to_buy
                            total_cash -= cost
                            investment_log.append(
                                f"{date.date()}: Bought {shares_to_buy:.2f} shares of {ticker} at ${price:.2f} (cost: ${cost:.2f})"
                            )
                    else:
                        # Step 4.8.7: Log when funds are insufficient
                        add_funds_needed = True
                        add_funds_dates.append(
                            (str(date.date()), ticker, price, total_cash)
                        )
                        investment_log.append(
                            f"{date.date()}: Not enough cash to buy {ticker} at ${price:.2f}. Available: ${total_cash:.2f}. Please add more funds."
                        )

        # Step 4.9: Calculate final portfolio value and performance metrics
        final_prices = stock_data.iloc[-1]  # Latest prices for each stock
        total_value = 0.0
        returns = {}  # Absolute returns for each ticker
        total_invested_per_stock = {}  # Amount invested in each stock
        percent_allocation_per_stock = {}  # Percentage allocation for each stock
        percent_return_per_stock = {}  # Percentage return for each stock
        total_invested = 0.0
        
        # Step 4.10: Calculate total amount invested per stock
        for idx, ticker in enumerate(tickers):
            # Calculate how much was actually invested in this stock
            if interval == "single_shot":
                # Step 4.10.1: For single-shot, only one purchase at first date
                first_date = stock_data.index[0]
                price = stock_data.loc[first_date][ticker]
                shares_bought = holdings[ticker]
                invested = shares_bought * price
            else:
                # Step 4.10.2: For DCA, sum all purchases from the log
                invested = 0.0
                for log in investment_log:
                    if f"shares of {ticker}" in log and "Bought" in log:
                        # Extract cost from log string
                        try:
                            cost_str = log.split("(cost: $")[-1].split(")")[0]
                            invested += float(cost_str)
                        except Exception:
                            pass
            total_invested_per_stock[ticker] = invested
            total_invested += invested
            
        # Step 4.11: Calculate percentage allocations and returns
        for ticker in tickers:
            invested = total_invested_per_stock[ticker]
            holding_value = holdings[ticker] * final_prices[ticker]  # Current value of holdings
            returns[ticker] = holding_value - invested  # Absolute return
            total_value += holding_value
            
            # Calculate percentage allocation (what % of total investment went to this stock)
            percent_allocation_per_stock[ticker] = (
                (invested / total_invested * 100) if total_invested > 0 else 0.0
            )
            
            # Calculate percentage return (how much % this stock gained/lost)
            percent_return_per_stock[ticker] = (
                ((holding_value - invested) / invested * 100) if invested > 0 else 0.0
            )
        total_value += total_cash  # Add remaining cash to total portfolio value

        # Step 4.12: Store investment summary results in state
        self.state['state']["investment_summary"] = {
            "holdings": holdings,  # Number of shares owned for each ticker
            "final_prices": final_prices.to_dict(),  # Current prices
            "cash": total_cash,  # Remaining cash
            "returns": returns,  # Absolute returns per ticker
            "total_value": total_value,  # Total portfolio value
            "investment_log": investment_log,  # Transaction history
            "add_funds_needed": add_funds_needed,  # Whether more funds were needed
            "add_funds_dates": add_funds_dates,  # Dates when funds were insufficient
            "total_invested_per_stock": total_invested_per_stock,  # Investment per ticker
            "percent_allocation_per_stock": percent_allocation_per_stock,  # Allocation %
            "percent_return_per_stock": percent_return_per_stock,  # Return %
        }
        self.state['state']["available_cash"] = total_cash  # Update available cash in state

        # ===============================================================================
        # BENCHMARK COMPARISON - Portfolio vs SPY (S&P 500)
        # ===============================================================================
        
        # Step 4.13: Get SPY (S&P 500) prices for benchmark comparison
        spy_ticker = "SPY"
        spy_prices = None
        try:
            # Download SPY data for the same time period
            spy_prices = yf.download(
                spy_ticker,
                period=f"{len(stock_data)//4}y" if len(stock_data) > 4 else "1y",
                interval="3mo",
                start=stock_data.index[0],
                end=stock_data.index[-1],
            )["Close"]
            # Align SPY prices to our stock_data dates using forward fill
            spy_prices = spy_prices.reindex(stock_data.index, method="ffill")
        except Exception as e:
            print("Error fetching SPY data:", e)
            # Create dummy SPY data if fetch fails
            spy_prices = pd.Series([None] * len(stock_data), index=stock_data.index)

        # Step 4.14: Simulate investing the same total amount in SPY for comparison
        spy_shares = 0.0
        spy_cash = total_invested  # Use same amount invested in our portfolio
        spy_invested = 0.0
        spy_investment_log = []
        
        if interval == "single_shot":
            # Step 4.14.1: Single-shot SPY investment
            first_date = stock_data.index[0]
            spy_price = spy_prices.loc[first_date]
            if isinstance(spy_price, pd.Series):
                spy_price = spy_price.iloc[0]
            if not pd.isna(spy_price):
                spy_shares = spy_cash // spy_price
                spy_invested = spy_shares * spy_price
                spy_cash -= spy_invested
                spy_investment_log.append(
                    f"{first_date.date()}: Bought {spy_shares:.2f} shares of SPY at ${spy_price:.2f} (cost: ${spy_invested:.2f})"
                )
        else:
            # Step 4.14.2: DCA SPY investment - spread equal amounts over time
            dca_amount = total_invested / len(stock_data)
            for date in stock_data.index:
                spy_price = spy_prices.loc[date]
                if isinstance(spy_price, pd.Series):
                    spy_price = spy_price.iloc[0]
                if not pd.isna(spy_price):
                    shares = dca_amount // spy_price
                    cost = shares * spy_price
                    spy_shares += shares
                    spy_cash -= cost
                    spy_invested += cost
                    spy_investment_log.append(
                        f"{date.date()}: Bought {shares:.2f} shares of SPY at ${spy_price:.2f} (cost: ${cost:.2f})"
                    )

        # Step 4.15: Build performance comparison data for charting
        performanceData = []
        running_holdings = holdings.copy()
        running_cash = total_cash
        
        for date in stock_data.index:
            # Step 4.15.1: Calculate portfolio value at each date
            port_value = (
                sum(
                    running_holdings[t] * stock_data.loc[date][t]
                    for t in tickers
                    if not pd.isna(stock_data.loc[date][t])
                )
                # Note: Not adding cash here since we want pure investment performance
            )
            
            # Step 4.15.2: Calculate SPY value at each date
            spy_price = spy_prices.loc[date]
            if isinstance(spy_price, pd.Series):
                spy_price = spy_price.iloc[0]
            spy_val = (
                spy_shares * spy_price + spy_cash if not pd.isna(spy_price) else None
            )
            
            # Step 4.15.3: Add data point for this date
            performanceData.append(
                {
                    "date": str(date.date()),
                    "portfolio": float(port_value) if port_value is not None else None,
                    "spy": float(spy_val) if spy_val is not None else None,
                }
            )

        # Step 4.16: Add performance data to investment summary
        self.state['state']["investment_summary"]["performanceData"] = performanceData

        # Step 4.17: Compose summary message for user
        if add_funds_needed:
            msg = "Some investments could not be made due to insufficient funds. Please add more funds to your wallet.\n"
            for d, t, p, c in add_funds_dates:
                msg += f"On {d}, not enough cash for {t}: price ${p:.2f}, available ${c:.2f}\n"
        else:
            msg = "All investments were made successfully.\n"
        msg += f"\nFinal portfolio value: ${total_value:.2f}\n"
        msg += "Returns by ticker (percent and $):\n"
        for ticker in tickers:
            percent = percent_return_per_stock[ticker]
            abs_return = returns[ticker]
            msg += f"{ticker}: {percent:.2f}% (${abs_return:.2f})\n"

        # Step 4.18: Add tool message indicating data extraction is complete
        self.state['state']["messages"].append(
            ToolMessage(
                role="tool",
                id=str(uuid.uuid4()),
                content="The relevant details had been extracted",
                tool_call_id=self.state['state']["messages"][-1].tool_calls[0].id,
            )
        )

        # Step 4.19: Add assistant message with chart rendering tool call
        self.state['state']["messages"].append(
            AssistantMessage(
                role="assistant",
                tool_calls=[
                    {
                        "id": str(uuid.uuid4()),
                        "type": "function",
                        "function": {
                            "name": "render_standard_charts_and_table",
                            "arguments": json.dumps(
                                {"investment_summary": self.state['state']["investment_summary"]}
                            ),
                        },
                    }
                ],
                id=str(uuid.uuid4()),
            )
        )
        
        # Step 4.20: Mark allocation calculation as completed
        index = len(self.state['state']["tool_logs"]) - 1
        self.state.get("emit_event")(
            StateDeltaEvent(
                type=EventType.STATE_DELTA,
                delta=[
                    {
                        "op": "replace",
                        "path": f"/tool_logs/{index}/status",
                        "value": "completed",
                    }
                ],
            )
        )
        await asyncio.sleep(0)
        return "insights"  # Proceed to insights generation
    
    @listen("allocation")
    async def insights(self):
        """
        Step 5: Generate bull/bear insights about the selected stocks
        - Use OpenAI to generate positive and negative analysis
        - Add insights to the investment summary for balanced perspective
        """
        # Step 5.1: Ensure we have tool calls from previous step
        if self.state['state']['messages'][-1].tool_calls is None:
            return "end"
            
        # Step 5.2: Create tool log for insights generation
        tool_log_id = str(uuid.uuid4())
        self.state['state']["tool_logs"].append(
            {
                "id": tool_log_id,
                "message": "Extracting Key insights",
                "status": "processing",
            }
        )
        
        # Step 5.3: Emit state change to update UI
        self.state.get("emit_event")(
            StateDeltaEvent(
                type=EventType.STATE_DELTA,
                delta=[
                    {
                        "op": "add",
                        "path": "/tool_logs/-",
                        "value": {
                            "message": "Extracting Key insights",
                            "status": "processing",
                            "id": tool_log_id,
                        },
                    }
                ],
            )
        )
        await asyncio.sleep(0)
        
        # Step 5.4: Extract ticker symbols for insights analysis
        tickers = self.be_arguments['ticker_symbols']
        
        # Step 5.5: Call Gemini to generate bull/bear insights
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel(
            model_name="gemini-2.0-flash-exp",
            tools=[generate_insights]
        )

        response = model.generate_content(
            [
                {"role": "user", "parts": [insights_prompt + "\n\nTickers: " + json.dumps(tickers)]}
            ],
            generation_config=genai.types.GenerationConfig(
                temperature=0.3,
            )
        )

        # Step 5.6: Process the insights response
        if response.candidates and response.candidates[0].function_calls:
            # Step 5.6.1: Extract existing arguments from chart rendering tool call
            args_dict = json.loads(self.state['state']["messages"][-1].tool_calls[0].function.arguments)

            # Step 5.6.2: Add the generated insights to the arguments
            insights_data = {}
            for fc in response.candidates[0].function_calls:
                if fc.name == "generate_insights":
                    insights_data = fc.args

            args_dict["insights"] = insights_data

            # Step 5.6.3: Update the tool call arguments with insights included
            self.state['state']["messages"][-1].tool_calls[0].function.arguments = json.dumps(args_dict)
        else:
            # Step 5.6.4: If insights generation failed, set empty insights
            self.state['state']["insights"] = {}
            
        # Step 5.7: Mark insights extraction as completed
        index = len(self.state['state']["tool_logs"]) - 1
        self.state.get("emit_event")(
            StateDeltaEvent(
                type=EventType.STATE_DELTA,
                delta=[
                    {
                        "op": "replace",
                        "path": f"/tool_logs/{index}/status",
                        "value": "completed",
                    }
                ],
            )
        )
        await asyncio.sleep(0)
        return "end"  # All steps complete, proceed to end
    
    @listen(or_("chat", "insights"))
    def end(self):
        """
        Step 6: Final step - return the complete state
        - This method is called from either 'chat' (if no investment data) 
          or 'insights' (after successful analysis)
        - Returns the final state with all analysis results
        """
        return self.state


# ===============================================================================
# UTILITY FUNCTIONS
# ===============================================================================

def convert_tool_call(tc):
    """
    Utility function to convert OpenAI tool call format to our internal format
    
    Args:
        tc: OpenAI tool call object
        
    Returns:
        dict: Formatted tool call dictionary compatible with our message system
    """
    return {
        "id": tc.id,
        "type": "function",
        "function": {
            "name": tc.function.name,
            "arguments": tc.function.arguments,
        },
    }
