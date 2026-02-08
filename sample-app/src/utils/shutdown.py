import signal
import sys
import logging
from typing import Callable, List
import atexit

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """
    Manages graceful shutdown of the application.
    Registers cleanup handlers and responds to termination signals.
    """
    
    def __init__(self):
        self.shutdown_handlers: List[Callable] = []
        self.is_shutting_down = False
        self._setup_signal_handlers()
    
    def _setup_signal_handlers(self):
        """Register signal handlers for graceful shutdown"""
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)
        
        # Register atexit handler
        atexit.register(self._cleanup)
        
        logger.info("Graceful shutdown handlers registered")
    
    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signal"""
        if self.is_shutting_down:
            logger.warning("Already shutting down, ignoring signal")
            return
        
        signal_name = signal.Signals(signum).name
        logger.info(f"Received {signal_name}, initiating graceful shutdown")
        
        self.is_shutting_down = True
        self._cleanup()
        
        logger.info("Graceful shutdown complete")
        sys.exit(0)
    
    def _cleanup(self):
        """Execute all registered cleanup handlers"""
        if not self.shutdown_handlers:
            logger.info("No cleanup handlers to execute")
            return
        
        logger.info(f"Executing {len(self.shutdown_handlers)} cleanup handlers")
        
        for handler in self.shutdown_handlers:
            try:
                handler_name = getattr(handler, '__name__', str(handler))
                logger.debug(f"Executing cleanup handler: {handler_name}")
                handler()
            except Exception as e:
                logger.error(
                    f"Error in cleanup handler {handler_name}: {e}",
                    exc_info=True
                )
    
    def register_cleanup(self, handler: Callable):
        """
        Register a cleanup handler to be called on shutdown.
        
        Args:
            handler: Callable to execute during shutdown
        """
        self.shutdown_handlers.append(handler)
        handler_name = getattr(handler, '__name__', str(handler))
        logger.debug(f"Registered cleanup handler: {handler_name}")
    
    def unregister_cleanup(self, handler: Callable):
        """
        Unregister a cleanup handler.
        
        Args:
            handler: Callable to remove from cleanup handlers
        """
        if handler in self.shutdown_handlers:
            self.shutdown_handlers.remove(handler)
            handler_name = getattr(handler, '__name__', str(handler))
            logger.debug(f"Unregistered cleanup handler: {handler_name}")


# Global shutdown manager instance
_shutdown_manager = None


def get_shutdown_manager() -> GracefulShutdown:
    """Get the global shutdown manager instance"""
    global _shutdown_manager
    if _shutdown_manager is None:
        _shutdown_manager = GracefulShutdown()
    return _shutdown_manager


def register_cleanup_handler(handler: Callable):
    """
    Convenience function to register cleanup handler.
    
    Args:
        handler: Callable to execute during shutdown
    """
    manager = get_shutdown_manager()
    manager.register_cleanup(handler)