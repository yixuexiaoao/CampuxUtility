import time, os, uuid

def generate_data_path(suffix: str = "html", namespace: str = "default") -> tuple[str, str]:
    """生成唯一的数据文件路径
    
    根据指定的文件后缀和命名空间生成唯一的文件路径。
    文件名格式：{namespace}_{timestamp}_{uuid}.{suffix}
    
    Args:
        suffix (str, optional): 文件后缀名，默认为"html"
        namespace (str, optional): 命名空间，用于文件分类，默认为"default"
        
    Returns:
        tuple[str, str]: 返回相对路径和绝对路径的元组
    """
    # 确保data目录存在
    os.makedirs("data", exist_ok=True)
    
    # 生成唯一文件名：命名空间_时间戳_UUID前8位.后缀
    filename = f"{namespace}_{int(time.time())}_{uuid.uuid4().hex[:8]}.{suffix}"
    
    # 同时返回相对路径和绝对路径
    relative_path = os.path.join("data", filename)
    absolute_path = os.path.abspath(relative_path)
    
    return relative_path, absolute_path